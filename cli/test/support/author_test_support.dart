// Shared helpers for the author-signing tests: a fake `openssl` (hermetic), the
// real-OpenSSL and registry-checkout gates, and a synthetic registry builder.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:hqplugin/src/author_signing.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

// ─────────────────────────────────────────────────────────────────────────────
// A fake openssl
// ─────────────────────────────────────────────────────────────────────────────

/// The secret every fake private key contains. No test output may carry it.
const String kFakeSecretMarker = 'FAKE-PRIVATE-KEY-SECRET-MARKER';

/// A stand-in `openssl` CLI: just enough of genpkey / pkey / pkeyutl for the
/// signer, with signatures that verify only under the matching "public key".
/// It writes real files (the signer reads them back) but does no cryptography.
class FakeOpenSsl {
  String versionLine = 'OpenSSL 3.6.0 1 Jan 2026 (Library: OpenSSL 3.6.0)';

  /// False: every call throws a ProcessException (no such executable).
  bool installed = true;

  /// False: `genpkey -algorithm ML-DSA-65` fails, as on OpenSSL < 3.5.
  bool pq = true;

  /// Non-zero: `pkeyutl -sign` exits with it, printing [signStderr].
  int signExit = 0;
  String signStderr = 'sign failed';

  /// A signature of this many bytes instead of the right size, when set.
  int? signatureBytes;

  /// False: `pkeyutl -verify` reports a verification failure.
  bool verifies = true;

  /// Non-zero: `genpkey` for ED25519 exits with it.
  int genEd25519Exit = 0;

  /// Verification results used before [verifies] applies (first call first).
  final List<bool> verifyQueue = [];

  final List<List<String>> calls = [];

  /// The executable of every call.
  final List<String> exes = [];

  /// Every temp directory a message or signature was written into.
  final Set<String> tempDirs = {};

  Future<ProcessResult> call(String exe, List<String> args) async {
    if (!installed) {
      throw ProcessException(exe, args, 'No such file or directory', 2);
    }
    calls.add(args);
    exes.add(exe);
    for (final a in args) {
      if (p.basename(a) == 'msg' || p.basename(a) == 'sig') {
        tempDirs.add(p.dirname(a));
      }
    }
    switch (args) {
      case ['version']:
        return ProcessResult(1, 0, '$versionLine\n', '');
      case ['genpkey', '-algorithm', final alg, '-out', final out]:
        if (alg == 'ML-DSA-65' && !pq) {
          return ProcessResult(1, 1, '', 'Algorithm ML-DSA-65 not found\n');
        }
        if (alg == 'ED25519' && genEd25519Exit != 0) {
          return ProcessResult(1, genEd25519Exit, '', 'genpkey failed\n');
        }
        writeKey(out, alg, 'secret-${calls.length}');
        return ProcessResult(1, 0, '', '');
      case [
        'pkey',
        '-in',
        final key,
        '-pubout',
        '-outform',
        'DER',
        '-out',
        final out,
      ]:
        final k = _readKey(key);
        if (k == null) return ProcessResult(1, 1, '', 'cannot read key\n');
        final alg = _algOf(k.alg);
        final raw = _expand(k.id, alg.publicKeyBytes);
        File(out).writeAsBytesSync([...alg.spkiPrefix, ...raw]);
        return ProcessResult(1, 0, '', '');
      case ['pkey', '-in', final key, '-pubout', '-out', final out]:
        final k = _readKey(key);
        if (k == null) return ProcessResult(1, 1, '', 'cannot read key\n');
        File(out).writeAsStringSync(
          '-----BEGIN PUBLIC KEY-----\nFAKEPUB ${k.alg} ${k.id}\n'
          '-----END PUBLIC KEY-----\n',
        );
        return ProcessResult(1, 0, '', '');
      case [
        'pkeyutl',
        '-sign',
        '-inkey',
        final key,
        '-rawin',
        '-in',
        final msg,
        '-out',
        final out,
      ]:
        if (signExit != 0) return ProcessResult(1, signExit, '', signStderr);
        final k = _readKey(key);
        if (k == null) return ProcessResult(1, 1, '', 'cannot read key\n');
        final alg = _algOf(k.alg);
        File(out).writeAsBytesSync(
          _signature(
            k.id,
            File(msg).readAsBytesSync(),
            signatureBytes ?? alg.signatureBytes,
          ),
        );
        return ProcessResult(1, 0, '', '');
      case [
        'pkeyutl',
        '-verify',
        '-pubin',
        '-inkey',
        final pub,
        '-rawin',
        '-in',
        final msg,
        '-sigfile',
        final sigFile,
      ]:
        final m = RegExp(
          r'FAKEPUB (\S+) (\S+)',
        ).firstMatch(File(pub).readAsStringSync());
        if (m == null) return ProcessResult(1, 1, '', 'bad public key\n');
        final alg = _algOf(m.group(1)!);
        final want = _signature(
          m.group(2)!,
          File(msg).readAsBytesSync(),
          alg.signatureBytes,
        );
        final got = File(sigFile).readAsBytesSync();
        final allowed = verifyQueue.isNotEmpty
            ? verifyQueue.removeAt(0)
            : verifies;
        final ok = allowed && _same(want, got);
        return ok
            ? ProcessResult(1, 0, 'Signature Verified Successfully\n', '')
            : ProcessResult(1, 1, 'Signature Verification Failure\n', '');
    }
    return ProcessResult(1, 2, '', 'fake openssl: unexpected args $args\n');
  }

  /// Writes a fake private key of [alg] (`ED25519` / `ML-DSA-65`).
  static void writeKey(String path, String alg, String secret) {
    File(path).writeAsStringSync(
      '-----BEGIN PRIVATE KEY-----\n'
      'FAKE $alg $kFakeSecretMarker-$secret\n'
      '-----END PRIVATE KEY-----\n',
    );
  }

  /// A directory holding fake `ed25519.pem` and `ml-dsa-65.pem`.
  static Directory keyDir({String secret = 'one'}) {
    final dir = Directory.systemTemp.createTempSync('hqplugin_fakekeys_');
    addTearDown(() {
      if (dir.existsSync()) dir.deleteSync(recursive: true);
    });
    writeKey(p.join(dir.path, kEd25519KeyFile), 'ED25519', secret);
    writeKey(p.join(dir.path, kMlDsa65KeyFile), 'ML-DSA-65', secret);
    return dir;
  }

  static HybridAlgorithm _algOf(String name) =>
      name == 'ML-DSA-65' ? HybridAlgorithm.mlDsa65 : HybridAlgorithm.ed25519;

  static ({String alg, String id})? _readKey(String path) {
    final f = File(path);
    if (!f.existsSync()) return null;
    final text = f.readAsStringSync();
    final m = RegExp(r'FAKE (\S+) ').firstMatch(text);
    if (m == null) return null;
    return (alg: m.group(1)!, id: sha256.convert(utf8.encode(text)).toString());
  }

  static Uint8List _expand(String seed, int n) {
    final out = <int>[];
    var block = sha256.convert(utf8.encode(seed)).bytes;
    while (out.length < n) {
      out.addAll(block);
      block = sha256.convert(block).bytes;
    }
    return Uint8List.fromList(out.sublist(0, n));
  }

  static Uint8List _signature(String id, List<int> msg, int n) =>
      _expand('$id|${base64.encode(msg)}', n);

  static bool _same(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Real OpenSSL and the registry checkout
// ─────────────────────────────────────────────────────────────────────────────

/// What the `openssl` this run uses ($HQPLUGIN_OPENSSL, else PATH) can do.
class OpenSslCaps {
  OpenSslCaps(this.executable, this.version, this.ed25519, this.mlDsa);
  final String executable;
  final String? version;
  final bool ed25519;
  final bool mlDsa;
}

String get opensslExecutable =>
    resolveOpenSslExecutable(null, Platform.environment);

OpenSslCaps detectOpenSsl() {
  final exe = opensslExecutable;
  final ProcessResult v;
  try {
    v = Process.runSync(exe, ['version']);
  } on ProcessException {
    return OpenSslCaps(exe, null, false, false);
  }
  if (v.exitCode != 0) return OpenSslCaps(exe, null, false, false);
  final dir = Directory.systemTemp.createTempSync('hqplugin_caps_');
  try {
    bool gen(String alg) =>
        Process.runSync(exe, [
          'genpkey',
          '-algorithm',
          alg,
          '-out',
          p.join(dir.path, '$alg.pem'),
        ]).exitCode ==
        0;
    return OpenSslCaps(
      exe,
      '${v.stdout}'.trim().split('\n').first,
      gen('ED25519'),
      gen('ML-DSA-65'),
    );
  } finally {
    dir.deleteSync(recursive: true);
  }
}

/// `HQPLUGIN_REQUIRE_OPENSSL_PQ=1` (CI legs that provide OpenSSL 3.5+): the
/// ML-DSA tests fail instead of skipping when OpenSSL cannot do ML-DSA-65.
bool get requirePq =>
    Platform.environment['HQPLUGIN_REQUIRE_OPENSSL_PQ'] == '1';

bool get requireRegistry =>
    Platform.environment['HELLOHQ_REQUIRE_REGISTRY'] == '1';

/// A `skip:` value for tests that need real OpenSSL Ed25519: null to run.
String? skipUnlessEd25519(OpenSslCaps c) =>
    c.ed25519 ? null : 'no usable openssl (HQPLUGIN_OPENSSL / PATH)';

/// A `skip:` value for tests that need OpenSSL 3.5+ (ML-DSA-65). With
/// `HQPLUGIN_REQUIRE_OPENSSL_PQ=1` it is null, so the test runs and fails.
String? skipUnlessPq(OpenSslCaps c) => c.mlDsa || requirePq
    ? null
    : 'openssl ${c.version ?? '(none)'} has no ML-DSA-65 '
          '(needs OpenSSL 3.5+; set HQPLUGIN_OPENSSL)';

/// The plugin-registry checkout ($HELLOHQ_PLUGIN_REGISTRY_DIR), or null.
Directory? registryCheckout() {
  final env = Platform.environment['HELLOHQ_PLUGIN_REGISTRY_DIR'];
  if (env == null || env.isEmpty) return null;
  final dir = Directory(env);
  return dir.existsSync() ? dir : null;
}

/// A `skip:` value for tests that need a registry checkout. With
/// `HELLOHQ_REQUIRE_REGISTRY=1` it is null, so [registryRoot] fails the test.
String? skipUnlessRegistry() => registryCheckout() != null || requireRegistry
    ? null
    : 'plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)';

/// The registry checkout, failing the test when there is none.
Directory registryRoot() {
  final dir = registryCheckout();
  if (dir == null) {
    fail(
      'plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)',
    );
  }
  return dir;
}

bool get hasNode {
  try {
    return Process.runSync('node', ['--version']).exitCode == 0;
  } on ProcessException {
    return false;
  }
}

/// Tests that run the registry's `scripts/author-signatures.mjs`. The script
/// arrives with plugin-registry#18; against a checkout that predates it these
/// skip, and they start running as soon as the checkout has it.
String? skipUnlessAuthorSignaturesScript() {
  final reg = registryCheckout();
  if (reg == null) {
    return requireRegistry
        ? null
        : 'plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)';
  }
  if (!File(
    p.join(reg.path, 'scripts', 'author-signatures.mjs'),
  ).existsSync()) {
    return 'this plugin-registry checkout has no scripts/author-signatures.mjs '
        '(plugin-registry#18)';
  }
  return hasNode ? null : 'node is not installed';
}

/// `PATH` with the directory of the configured openssl in front, so the
/// registry scripts (which run plain `openssl`) use the same binary.
Map<String, String> envWithOpensslFirst() {
  final exe = opensslExecutable;
  final path = Platform.environment['PATH'] ?? '';
  if (!p.isAbsolute(exe)) return {'PATH': path};
  return {'PATH': '${p.dirname(exe)}${Platform.isWindows ? ';' : ':'}$path'};
}

Future<ProcessResult> runNode(
  List<String> args, {
  String? workingDirectory,
  bool binaryStdout = false,
}) => Process.run(
  'node',
  args,
  workingDirectory: workingDirectory,
  environment: envWithOpensslFirst(),
  stdoutEncoding: binaryStdout ? null : systemEncoding,
);

Directory tempDir(String prefix) {
  final dir = Directory.systemTemp.createTempSync(prefix);
  addTearDown(() async {
    for (var attempt = 0; attempt < 12; attempt++) {
      try {
        if (dir.existsSync()) dir.deleteSync(recursive: true);
        return;
      } catch (_) {
        await Future<void>.delayed(const Duration(milliseconds: 250));
      }
    }
  });
  return dir;
}

// ─────────────────────────────────────────────────────────────────────────────
// A synthetic registry
// ─────────────────────────────────────────────────────────────────────────────

/// A registry directory (just authors/ and plugins/) in which [authorSlug] is
/// enrolled with [publicKeys] for [idPrefix], with a valid Ed25519 proof of
/// possession made by [ed25519KeyPath] through the registry's own
/// `author-cert.mjs statement` and openssl. [manifest] and [sidecar] are
/// written under plugins/<id>/.
Future<Directory> syntheticRegistry({
  required OpenSslTool tool,
  required Map<String, String> publicKeys,
  required String ed25519KeyPath,
  required Map<String, dynamic> manifest,
  String? sidecar,
  String authorSlug = 'test-author',
  String idPrefix = 'com.example.',
}) async {
  final reg = registryRoot();
  final dir = tempDir('hqplugin_synth_registry_');
  final authors = Directory(p.join(dir.path, 'authors'))..createSync();
  final author = <String, dynamic>{
    'slug': authorSlug,
    'identity': {'oidc_issuer': 'https://github.com', 'oidc_subject': '4242'},
    'id_prefixes': [idPrefix],
    'public_keys': publicKeys,
    'tier': 'author',
  };
  final authorFile = File(p.join(authors.path, '$authorSlug.json'))
    ..writeAsStringSync(jsonEncode(author));
  final stmt = await runNode([
    p.join(reg.path, 'scripts', 'author-cert.mjs'),
    'statement',
    authorFile.path,
  ], binaryStdout: true);
  expect(stmt.exitCode, 0, reason: '${stmt.stderr}');
  final proof = await tool.sign(
    HybridAlgorithm.ed25519,
    ed25519KeyPath,
    stmt.stdout as List<int>,
  );
  author['proof'] = {'ed25519': base64.encode(proof)};
  authorFile.writeAsStringSync(jsonEncode(author));

  final plugin = Directory(
    p.join(dir.path, 'plugins', manifest['id'] as String),
  )..createSync(recursive: true);
  File(
    p.join(plugin.path, 'manifest.json'),
  ).writeAsStringSync(jsonEncode(manifest));
  if (sidecar != null) {
    File(
      p.join(plugin.path, 'author-signatures.json'),
    ).writeAsStringSync(sidecar);
  }
  return dir;
}
