// Author-held artifact signatures: the artifact statement and an OpenSSL-backed
// hybrid (Ed25519 + ML-DSA-65) signer.
//
// An author who holds a hybrid key submits `plugins/<id>/author-signatures.json`
// beside the manifest in the registry PR. Both signatures cover the *artifact
// statement* (the registry's scripts/artifact-statement.mjs, format v1), not
// the plugin file. This file is the Dart twin of that script and of the `sign`
// command of scripts/author-signatures.mjs: the two are interchangeable, and
// the same PEM key files work with both.
//
// Both algorithms go through the `openssl` CLI (Ed25519 needs OpenSSL 3.0,
// ML-DSA-65 needs OpenSSL 3.5), so the CLI adds no crypto dependency. The
// private key is only ever a *path* handed to openssl: it is never read into a
// Dart string, and nothing here prints it. Messages and signatures pass
// through a fresh private temp directory that is deleted afterwards.
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:path/path.dart' as p;

/// The statement format line (scripts/artifact-statement.mjs).
const String kArtifactStatementVersion = 'hellohq-plugin-artifact/v1';

/// The trust tier a statement is built for when an author signs: the registry
/// team sets `verified` at merge, and the statement covers it.
const String kAuthorSignedTier = 'verified';

/// Names of the private key files `keys generate` writes and `publish
/// --sign-key <dir>` reads.
const String kEd25519KeyFile = 'ed25519.pem';
const String kMlDsa65KeyFile = 'ml-dsa-65.pem';
const String kPublicKeysFile = 'public-keys.json';

final RegExp _hex64 = RegExp(r'^[a-f0-9]{64}$');

/// A manifest value that would make the artifact statement ambiguous or
/// invalid (a line break, a hash that is not lowercase hex).
class StatementException implements Exception {
  const StatementException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// The artifact statement bytes for [manifest], exactly as the registry's
/// `artifactStatement()` builds them: UTF-8, seven LF-terminated lines.
///
/// `trust_tier` defaults to `community` and `execution_mode` to `wasm`;
/// `ui_bundle_hash_sha256` is empty when absent. Throws [StatementException]
/// with the registry's wording on a value with a line break or a hash that is
/// not 64 lowercase hex characters.
Uint8List artifactStatement(Map<String, dynamic> manifest) {
  String field(String name, Object? value) {
    if (value is! String) {
      throw StatementException('artifact statement: $name must be a string');
    }
    if (value.contains('\r') || value.contains('\n')) {
      throw StatementException(
        'artifact statement: $name contains a line break',
      );
    }
    return '$name=$value\n';
  }

  String hash(String name, Object? value, {bool optional = false}) {
    if (optional && value == null) return field(name, '');
    if (value is! String || !_hex64.hasMatch(value)) {
      throw StatementException(
        'artifact statement: $name must be 64 lowercase hex',
      );
    }
    return field(name, value);
  }

  final text =
      '$kArtifactStatementVersion\n'
      '${field('id', manifest['id'])}'
      '${field('version', manifest['version'])}'
      '${field('trust_tier', manifest['trust_tier'] ?? 'community')}'
      '${field('execution_mode', manifest['execution_mode'] ?? 'wasm')}'
      '${hash('content_hash_sha256', manifest['content_hash_sha256'])}'
      '${hash('ui_bundle_hash_sha256', manifest['ui_bundle_hash_sha256'], optional: true)}';
  return Uint8List.fromList(utf8.encode(text));
}

/// [manifest] as it will be once the registry team has set `trust_tier:
/// verified`. An author signs this view, not the manifest in the PR: the PR
/// leaves `trust_tier` unset and the signed statement already names `verified`.
Map<String, dynamic> verifiedTierView(Map<String, dynamic> manifest) => {
  ...manifest,
  'trust_tier': kAuthorSignedTier,
};

// ─────────────────────────────────────────────────────────────────────────────
// Algorithms
// ─────────────────────────────────────────────────────────────────────────────

/// One half of the hybrid key: how OpenSSL names it and how big its raw
/// public key and signature are.
class HybridAlgorithm {
  const HybridAlgorithm._(
    this.sigAlg,
    this.opensslName,
    this.publicKeyBytes,
    this.signatureBytes,
    this._spkiPrefixHex,
  );

  /// `sig_alg` in author-signatures.json and the key name in `public_keys`.
  final String sigAlg;

  /// The `-algorithm` name `openssl genpkey` takes.
  final String opensslName;
  final int publicKeyBytes;
  final int signatureBytes;
  final String _spkiPrefixHex;

  /// The SubjectPublicKeyInfo bytes that precede the raw public key.
  Uint8List get spkiPrefix => Uint8List.fromList([
    for (var i = 0; i < _spkiPrefixHex.length; i += 2)
      int.parse(_spkiPrefixHex.substring(i, i + 2), radix: 16),
  ]);

  static const ed25519 = HybridAlgorithm._(
    'ed25519',
    'ED25519',
    32,
    64,
    '302a300506032b6570032100',
  );
  static const mlDsa65 = HybridAlgorithm._(
    'ml-dsa-65',
    'ML-DSA-65',
    1952,
    3309,
    '308207b2300b0609608648016503040312038207a100',
  );
  static const all = [ed25519, mlDsa65];
}

/// The two signatures of `author-signatures.json`.
class AuthorSignatures {
  AuthorSignatures({required this.ed25519, required this.mlDsa65});

  final Uint8List ed25519;
  final Uint8List mlDsa65;

  /// The file the registry reads: 2-space JSON, a trailing newline, Ed25519
  /// first (what `author-signatures.mjs sign` writes).
  String toSidecarJson() =>
      '${const JsonEncoder.withIndent('  ').convert({
        'signatures': [
          {'sig_alg': 'ed25519', 'signature': base64.encode(ed25519)},
          {'sig_alg': 'ml-dsa-65', 'signature': base64.encode(mlDsa65)},
        ],
      })}\n';

  /// Parses an `author-signatures.json` body the way the registry's
  /// `parseAuthorSignatures` does: exactly one ed25519 and one ml-dsa-65
  /// signature, canonical base64, the right lengths. Throws [FormatException].
  factory AuthorSignatures.parseSidecar(String json) {
    final Object? body;
    try {
      body = jsonDecode(json);
    } on FormatException {
      throw const FormatException('author-signatures.json is not valid JSON');
    }
    final sigs = body is Map ? body['signatures'] : null;
    if (sigs is! List) {
      throw const FormatException(
        'author-signatures.json needs a signatures array',
      );
    }
    final byAlg = <String, Object?>{
      for (final s in sigs)
        if (s is Map && s['sig_alg'] is String)
          s['sig_alg'] as String: s['signature'],
    };
    final algs = byAlg.keys.toList()..sort();
    if (sigs.length != 2 || algs.join(',') != 'ed25519,ml-dsa-65') {
      throw const FormatException(
        'author-signatures.json needs exactly one ed25519 and one ml-dsa-65 '
        'signature',
      );
    }
    Uint8List decode(HybridAlgorithm alg) {
      final raw = byAlg[alg.sigAlg];
      if (raw is! String || !RegExp(r'^[A-Za-z0-9+/]+={0,2}$').hasMatch(raw)) {
        throw FormatException('${alg.sigAlg} signature must be base64');
      }
      final Uint8List bytes;
      try {
        bytes = base64.decode(raw);
      } on FormatException {
        throw FormatException('${alg.sigAlg} signature must be base64');
      }
      if (bytes.length != alg.signatureBytes || base64.encode(bytes) != raw) {
        throw FormatException(
          '${alg.sigAlg} signature must be ${alg.signatureBytes} bytes, base64',
        );
      }
      return bytes;
    }

    return AuthorSignatures(
      ed25519: decode(HybridAlgorithm.ed25519),
      mlDsa65: decode(HybridAlgorithm.mlDsa65),
    );
  }
}

/// Signs and verifies artifact statements with one author's hybrid key.
abstract interface class HybridSigner {
  /// Both signatures over [statement].
  Future<AuthorSignatures> sign(List<int> statement);

  /// Whether both [signatures] verify over [statement] under this signer's
  /// public keys.
  Future<bool> verify(List<int> statement, AuthorSignatures signatures);

  /// The raw public key of each half (32 and 1952 bytes).
  Map<HybridAlgorithm, Uint8List> get publicKeys;
}

/// A failure of the signer, with the exit code `hqplugin` ends with: 69 OpenSSL
/// missing or too old, 66 a key file is missing, 65 a key file openssl cannot
/// use, 70 an internal inconsistency (a signature of the wrong size, one that
/// does not verify).
class SignerException implements Exception {
  const SignerException(this.code, this.message);
  final int code;
  final String message;
  @override
  String toString() => message;
}

// ─────────────────────────────────────────────────────────────────────────────
// OpenSSL
// ─────────────────────────────────────────────────────────────────────────────

/// Runs one external command. `PublishCommandRunner` is assignable to this.
typedef OpenSslRunner =
    Future<ProcessResult> Function(String executable, List<String> arguments);

/// Where the OpenSSL binary comes from: `--openssl <path>`, else
/// `$HQPLUGIN_OPENSSL`, else `openssl` on PATH.
String resolveOpenSslExecutable(String? flag, Map<String, String> env) {
  if (flag != null && flag.isNotEmpty) return flag;
  final fromEnv = env['HQPLUGIN_OPENSSL'];
  if (fromEnv != null && fromEnv.isNotEmpty) return fromEnv;
  return 'openssl';
}

/// [raw] made safe to show: OpenSSL's error text does not normally carry key
/// material, but PEM armour, long base64 runs and hex dumps are dropped anyway,
/// and the result is capped.
String sanitizeOpenSslText(String raw) {
  final armour = RegExp(r'-----(BEGIN|END)');
  final base64Run = RegExp(r'[A-Za-z0-9+/=]{40,}');
  final hexDump = RegExp(r'^\s*([0-9a-fA-F]{2}[: ]){8,}');
  final kept = [
    for (final line in const LineSplitter().convert(raw))
      if (line.trim().isNotEmpty &&
          !armour.hasMatch(line) &&
          !base64Run.hasMatch(line) &&
          !hexDump.hasMatch(line))
        line.trim(),
  ].join('\n');
  return kept.length <= 600 ? kept : '${kept.substring(0, 600)}...';
}

/// The few `openssl` invocations author signing needs.
class OpenSslTool {
  OpenSslTool({required this.run, this.executable = 'openssl'});

  final OpenSslRunner run;
  final String executable;

  /// The first line of `openssl version`, once [preflight] has run.
  String? version;

  Future<ProcessResult> _exec(List<String> args) async {
    try {
      return await run(executable, args);
    } on ProcessException catch (ex) {
      throw SignerException(
        69,
        'OpenSSL was not found (tried "$executable": ${ex.message}).\n'
        '$_getOpenSsl',
      );
    }
  }

  static const String _getOpenSsl =
      '  Author signing needs OpenSSL 3.5 or newer (for ML-DSA-65). Get one:\n'
      '    - macOS: `brew install openssl@3`, then pass\n'
      '      --openssl "\$(brew --prefix openssl@3)/bin/openssl" (Homebrew\'s\n'
      '      OpenSSL is not on PATH)\n'
      '    - Linux: a distribution that ships OpenSSL 3.5+\n'
      '    - Windows: an OpenSSL 3.5+ build; the openssl bundled with Git for\n'
      '      Windows may be older (check `openssl version`)\n'
      '  Point hqplugin at it with --openssl <path> or \$HQPLUGIN_OPENSSL.';

  /// Checks that [executable] runs and can do ML-DSA-65 (OpenSSL 3.5+); a
  /// throwaway probe key is generated in a private temp directory. Throws
  /// [SignerException] 69 naming the version found.
  Future<String> preflight() async {
    final v = await _exec(const ['version']);
    final line = '${v.stdout}'
        .split('\n')
        .map((l) => l.trim())
        .firstWhere((l) => l.isNotEmpty, orElse: () => '');
    if (v.exitCode != 0) {
      throw SignerException(
        69,
        '"$executable version" failed: ${sanitizeOpenSslText('${v.stderr}')}\n'
        '$_getOpenSsl',
      );
    }
    version = line.isEmpty ? 'an unknown OpenSSL' : line;
    await _withTemp((dir) async {
      final probe = p.join(dir.path, 'probe.pem');
      final r = await _exec([
        'genpkey',
        '-algorithm',
        HybridAlgorithm.mlDsa65.opensslName,
        '-out',
        probe,
      ]);
      if (r.exitCode != 0) {
        throw SignerException(
          69,
          '$version (at "$executable") cannot create ML-DSA-65 keys; author '
          'signing needs OpenSSL 3.5 or newer.\n$_getOpenSsl',
        );
      }
    });
    return version!;
  }

  /// `openssl genpkey` of [alg] straight into [outPath] (openssl creates the
  /// file; its contents never pass through this process).
  Future<void> generateKey(HybridAlgorithm alg, String outPath) async {
    final r = await _exec([
      'genpkey',
      '-algorithm',
      alg.opensslName,
      '-out',
      outPath,
    ]);
    if (r.exitCode != 0) {
      throw SignerException(
        69,
        'openssl could not generate an ${alg.sigAlg} key: '
        '${sanitizeOpenSslText('${r.stderr}${r.stdout}')}',
      );
    }
  }

  /// The raw public key of the private key at [keyPath], read from the SPKI DER
  /// openssl derives. Fails (65) when the file is not a key of [alg].
  Future<Uint8List> rawPublicKey(HybridAlgorithm alg, String keyPath) {
    return _withTemp((dir) async {
      final der = p.join(dir.path, 'pub.der');
      final r = await _exec([
        'pkey',
        '-in',
        keyPath,
        '-pubout',
        '-outform',
        'DER',
        '-out',
        der,
      ]);
      if (r.exitCode != 0 || !File(der).existsSync()) {
        throw SignerException(
          65,
          'openssl could not read an ${alg.sigAlg} private key from '
          '$keyPath: ${sanitizeOpenSslText('${r.stderr}${r.stdout}')}',
        );
      }
      final bytes = File(der).readAsBytesSync();
      final prefix = alg.spkiPrefix;
      final ok =
          bytes.length == prefix.length + alg.publicKeyBytes &&
          [
            for (var i = 0; i < prefix.length; i++) bytes[i] == prefix[i],
          ].every((b) => b);
      if (!ok) {
        throw SignerException(
          65,
          '$keyPath is not an ${alg.sigAlg} private key (it must be a PEM '
          'from `hqplugin keys generate` or `openssl genpkey -algorithm '
          '${alg.opensslName}`).',
        );
      }
      return Uint8List.fromList(bytes.sublist(prefix.length));
    });
  }

  /// A raw signature over [message] by the key at [keyPath]
  /// (`pkeyutl -sign -rawin`: pure Ed25519, pure FIPS 204 ML-DSA with an empty
  /// context, as scripts/author-signatures.mjs signs).
  Future<Uint8List> sign(
    HybridAlgorithm alg,
    String keyPath,
    List<int> message,
  ) {
    return _withTemp((dir) async {
      final msg = File(p.join(dir.path, 'msg'))..writeAsBytesSync(message);
      final sig = p.join(dir.path, 'sig');
      final r = await _exec([
        'pkeyutl',
        '-sign',
        '-inkey',
        keyPath,
        '-rawin',
        '-in',
        msg.path,
        '-out',
        sig,
      ]);
      if (r.exitCode != 0 || !File(sig).existsSync()) {
        throw SignerException(
          65,
          'openssl could not sign with the ${alg.sigAlg} key $keyPath: '
          '${sanitizeOpenSslText('${r.stderr}${r.stdout}')}',
        );
      }
      final bytes = File(sig).readAsBytesSync();
      if (bytes.length != alg.signatureBytes) {
        throw SignerException(
          70,
          'openssl made a ${bytes.length}-byte ${alg.sigAlg} signature; '
          'expected ${alg.signatureBytes}.',
        );
      }
      return bytes;
    });
  }

  /// Whether [signature] verifies over [message] under the public key derived
  /// from the private key at [keyPath].
  Future<bool> verify(
    HybridAlgorithm alg,
    String keyPath,
    List<int> message,
    List<int> signature,
  ) {
    return _withTemp((dir) async {
      final pub = p.join(dir.path, 'pub.pem');
      final d = await _exec(['pkey', '-in', keyPath, '-pubout', '-out', pub]);
      if (d.exitCode != 0) {
        throw SignerException(
          65,
          'openssl could not derive the ${alg.sigAlg} public key from '
          '$keyPath: ${sanitizeOpenSslText('${d.stderr}${d.stdout}')}',
        );
      }
      return _verifyWithPublicPem(dir, alg, pub, message, signature);
    });
  }

  /// Whether [signature] verifies over [message] under the *raw* public key
  /// [rawPublicKey] (32 bytes for Ed25519, 1952 for ML-DSA-65), the form the
  /// registry stores. The key is wrapped in its SubjectPublicKeyInfo and handed
  /// to openssl as a PEM public key, so this checks the exact bytes an
  /// enrollment publishes rather than a key re-derived from a private file.
  Future<bool> verifyWithPublicKey(
    HybridAlgorithm alg,
    List<int> rawPublicKey,
    List<int> message,
    List<int> signature,
  ) {
    if (rawPublicKey.length != alg.publicKeyBytes) {
      throw SignerException(
        70,
        'an ${alg.sigAlg} public key is ${alg.publicKeyBytes} bytes, not '
        '${rawPublicKey.length}.',
      );
    }
    return _withTemp((dir) async {
      final b64 = base64.encode([...alg.spkiPrefix, ...rawPublicKey]);
      final body = StringBuffer();
      for (var i = 0; i < b64.length; i += 64) {
        body.writeln(b64.substring(i, math.min(i + 64, b64.length)));
      }
      final pub = File(p.join(dir.path, 'pub.pem'))
        ..writeAsStringSync(
          '-----BEGIN PUBLIC KEY-----\n$body-----END PUBLIC KEY-----\n',
        );
      return _verifyWithPublicPem(dir, alg, pub.path, message, signature);
    });
  }

  Future<bool> _verifyWithPublicPem(
    Directory dir,
    HybridAlgorithm alg,
    String pubPath,
    List<int> message,
    List<int> signature,
  ) async {
    final msg = File(p.join(dir.path, 'msg'))..writeAsBytesSync(message);
    final sig = File(p.join(dir.path, 'sig'))..writeAsBytesSync(signature);
    final r = await _exec([
      'pkeyutl',
      '-verify',
      '-pubin',
      '-inkey',
      pubPath,
      '-rawin',
      '-in',
      msg.path,
      '-sigfile',
      sig.path,
    ]);
    if (r.exitCode == 0) return true;
    final text = '${r.stdout}${r.stderr}';
    if (text.contains('Verification Failure')) return false;
    throw SignerException(
      70,
      'openssl could not check an ${alg.sigAlg} signature: '
      '${sanitizeOpenSslText(text)}',
    );
  }

  /// Runs [body] with a fresh private temp directory, deleted afterwards.
  Future<T> _withTemp<T>(Future<T> Function(Directory dir) body) async {
    final dir = await Directory.systemTemp.createTemp('hqplugin-sign-');
    try {
      return await body(dir);
    } finally {
      for (var attempt = 0; attempt < 5; attempt++) {
        try {
          dir.deleteSync(recursive: true);
          break;
        } catch (_) {
          if (!dir.existsSync()) break;
          // Windows can hold a handle on a fresh temp file briefly.
          await Future<void>.delayed(const Duration(milliseconds: 100));
        }
      }
    }
  }
}

/// A [HybridSigner] over two PEM private keys, using the `openssl` CLI.
class OpenSslHybridSigner implements HybridSigner {
  OpenSslHybridSigner._(
    this.tool,
    this.ed25519KeyPath,
    this.mlDsa65KeyPath,
    this.publicKeys,
  );

  final OpenSslTool tool;
  final String ed25519KeyPath;
  final String mlDsa65KeyPath;
  @override
  final Map<HybridAlgorithm, Uint8List> publicKeys;

  static final List<int> _probe = utf8.encode('hqplugin key check\n');

  /// Opens the keys in [keyDir] (`ed25519.pem`, `ml-dsa-65.pem`): checks
  /// OpenSSL, that each file is a key of the right algorithm, and that each
  /// signs and verifies a probe message, so a bad setup fails before anything
  /// irreversible happens.
  static Future<OpenSslHybridSigner> open(
    OpenSslTool tool,
    String keyDir,
  ) async {
    if (!Directory(keyDir).existsSync()) {
      throw SignerException(
        66,
        'the author key directory "$keyDir" does not exist.\n'
        '  Create keys with `hqplugin keys generate --out $keyDir`.',
      );
    }
    final ed = p.absolute(p.join(keyDir, kEd25519KeyFile));
    final ml = p.absolute(p.join(keyDir, kMlDsa65KeyFile));
    for (final (path, name) in [(ed, kEd25519KeyFile), (ml, kMlDsa65KeyFile)]) {
      if (!File(path).existsSync()) {
        throw SignerException(
          66,
          'no $name in the author key directory "$keyDir".\n'
          '  `hqplugin keys generate --out <dir>` writes both key files.',
        );
      }
    }
    await tool.preflight();
    final keys = {
      HybridAlgorithm.ed25519: await tool.rawPublicKey(
        HybridAlgorithm.ed25519,
        ed,
      ),
      HybridAlgorithm.mlDsa65: await tool.rawPublicKey(
        HybridAlgorithm.mlDsa65,
        ml,
      ),
    };
    final signer = OpenSslHybridSigner._(tool, ed, ml, keys);
    final probe = await signer.sign(_probe);
    if (!await signer.verify(_probe, probe)) {
      throw const SignerException(
        70,
        'the author keys signed a probe message that does not verify; '
        'nothing was signed for the registry.',
      );
    }
    return signer;
  }

  @override
  Future<AuthorSignatures> sign(List<int> statement) async {
    // One after the other: openssl runs are cheap and the order is stable.
    final ed = await tool.sign(
      HybridAlgorithm.ed25519,
      ed25519KeyPath,
      statement,
    );
    final ml = await tool.sign(
      HybridAlgorithm.mlDsa65,
      mlDsa65KeyPath,
      statement,
    );
    return AuthorSignatures(ed25519: ed, mlDsa65: ml);
  }

  @override
  Future<bool> verify(List<int> statement, AuthorSignatures signatures) async {
    final ed = await tool.verify(
      HybridAlgorithm.ed25519,
      ed25519KeyPath,
      statement,
      signatures.ed25519,
    );
    final ml = await tool.verify(
      HybridAlgorithm.mlDsa65,
      mlDsa65KeyPath,
      statement,
      signatures.mlDsa65,
    );
    return ed && ml;
  }
}
