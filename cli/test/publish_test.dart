// Tests for `hqplugin publish`. Every external command goes through a fake
// runner and every download through a fake HTTP GET, so nothing here touches
// the network, GitHub or the registry. (publish_network_test.dart is the one
// opt-in test against the real released hello-world.)
import 'dart:convert';
import 'dart:io';

import 'package:hqplugin/src/publish.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

// ─────────────────────────────────────────────────────────────────────────────
// Fixtures
// ─────────────────────────────────────────────────────────────────────────────

const _id = 'com.example.summary';
const _authorRepo = 'octocat/summary-plugin';
const _headSha = '0123456789abcdef0123456789abcdef01234567';
const _rawUpstream =
    'https://raw.githubusercontent.com/HelloHQ/plugin-registry/main/plugins/$_id/manifest.json';
const _servedWasmUrl =
    'https://github.com/$_authorRepo/releases/download/v1.0.0/plugin.wasm';
final _placeholder = '0' * 64;

/// A minimal valid core Wasm module header plus a payload byte.
final _wasmA = [0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, 0x0a];
final _wasmB = [0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, 0x0b];
final _uiZip = utf8.encode('PK\x03\x04 ui bundle');
final _python = utf8.encode('from hellohq_plugin_sdk import plugin\n');
final _iconA = utf8.encode(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
  '<path fill="currentColor" d="M4 4h16v16H4z"/></svg>',
);
final _iconB = utf8.encode(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
  '<path fill="currentColor" d="M2 2h20v20H2z"/></svg>',
);
const _servedIconUrl =
    'https://github.com/$_authorRepo/releases/download/v1.0.0/icon.svg';

Map<String, dynamic> _manifest([Map<String, dynamic> overrides = const {}]) {
  final m = <String, dynamic>{
    'id': _id,
    'name': 'Summary',
    'version': '1.0.0',
    'description': 'Summarises portfolios.',
    'author': 'Octo Cat',
    'author_url': 'https://github.com/octocat',
    'repo': 'https://github.com/$_authorRepo',
    'license': 'MIT',
    'min_host_version': '1.0.0',
    'execution_mode': 'wasm',
    'wasm_url': _servedWasmUrl,
    'content_hash_sha256': _placeholder,
    'ui_type': 'declarative',
    'licensing': {'kind': 'open_source', 'spdx': 'MIT'},
    'permissions': [
      {'id': 'read:portfolio_names'},
    ],
  };
  overrides.forEach((k, v) => v == null ? m.remove(k) : m[k] = v);
  return m;
}

String _releaseUrl(String tag, String name, [String repo = _authorRepo]) =>
    'https://github.com/$repo/releases/download/$tag/$name';

class Call {
  Call(this.exe, this.args, this.cwd);
  final String exe;
  final List<String> args;
  final String? cwd;
  String get line => '$exe ${args.join(' ')}';
  @override
  String toString() => line;
}

ProcessResult _ok([String stdout = '']) => ProcessResult(1, 0, stdout, '');
ProcessResult _fail(int code, [String stderr = '']) =>
    ProcessResult(1, code, '', stderr);

/// A fake GitHub + git + node + HTTP world. Knobs default to the happy path.
class FakeWorld {
  final calls = <Call>[];
  final fetched = <String>[];

  /// URL → served bytes.
  final served = <String, List<int>>{};

  /// URL → redirect target.
  final redirects = <String, String>{};

  String login = 'octocat';
  bool member = true;
  bool hasGh = true;
  bool hasNode = true;
  String gitStatus = '';
  String? origin = 'git@github.com:$_authorRepo.git';

  /// `gh release view` JSON, or null for "release not found".
  Map<String, dynamic>? release;

  /// Bytes GitHub serves after `gh release create`, by asset name (default:
  /// the uploaded file's bytes).
  final uploadedOverride = <String, List<int>>{};

  /// The registry's current manifest (raw URL and `git show`), or null.
  String? upstreamRaw;

  bool forkExists = true;
  bool upstreamRemoteExists = false;
  ProcessResult signatureCheck = _ok();
  ProcessResult verifyArtifacts = _ok('  wasm_url SHA-256 ok\n');
  ProcessResult commitResult = _ok();
  String prList = '[]';

  /// Snapshot of the clone when the registry scripts ran.
  String? cloneDir;
  String? writtenManifest;
  String? baseManifestArg;
  String? baseManifestContent;

  set upstream(Map<String, dynamic>? m) => upstreamRaw = m == null
      ? null
      : const JsonEncoder.withIndent('  ').convert(m);

  Iterable<String> get lines => calls.map((c) => c.line);

  bool saw(String prefix) => lines.any((l) => l.startsWith(prefix));

  static const mutatingPrefixes = [
    'gh release create',
    'gh release upload',
    'gh release edit',
    'gh repo fork',
    'gh repo clone',
    'git push',
    'git commit',
    'gh pr create',
    'gh pr edit',
  ];

  List<String> get mutatingCalls => [
    for (final l in lines)
      if (mutatingPrefixes.any(l.startsWith)) l,
  ];

  Future<HttpHop> get(Uri url, {required int maxBytes}) async {
    final u = url.toString();
    fetched.add(u);
    final to = redirects[u];
    if (to != null) return HttpHop(302, location: to);
    if (u == _rawUpstream) {
      return upstreamRaw == null
          ? const HttpHop(404)
          : HttpHop(200, body: utf8.encode(upstreamRaw!));
    }
    final body = served[u];
    if (body == null) return const HttpHop(404);
    if (body.length > maxBytes) {
      return HttpHop(200, body: body.sublist(0, maxBytes + 1), truncated: true);
    }
    return HttpHop(200, body: body);
  }

  Future<ProcessResult> run(
    String exe,
    List<String> args, {
    String? workingDirectory,
  }) async {
    calls.add(Call(exe, args, workingDirectory));
    if (args case ['--version']) {
      if ((exe == 'gh' && !hasGh) || (exe == 'node' && !hasNode)) {
        throw ProcessException(exe, args, 'No such file or directory', 2);
      }
      return _ok('$exe version 1\n');
    }
    if (exe == 'gh') return _gh(args);
    if (exe == 'git') return _git(args, workingDirectory);
    if (exe == 'node') return _node(args, workingDirectory!);
    return _fail(127, '$exe: not faked');
  }

  ProcessResult _gh(List<String> args) {
    switch (args) {
      case ['api', 'user', '--jq', '.login']:
        return _ok('$login\n');
      case ['api', final path, '--silent']
          when path.startsWith('orgs/HelloHQ/members/'):
        return member ? _ok() : _fail(1, 'gh: Not Found (HTTP 404)\n');
      case ['release', 'view', _, '--repo', _, '--json', 'isDraft,assets']:
        return release == null
            ? _fail(1, 'release not found\n')
            : _ok(jsonEncode(release));
      case ['release', 'create', final tag, ...final rest]:
        final repo = rest[rest.indexOf('--repo') + 1];
        for (final f in rest.takeWhile((a) => !a.startsWith('--'))) {
          final name = p.basename(f);
          served[_releaseUrl(tag, name, repo)] =
              uploadedOverride[name] ?? File(f).readAsBytesSync();
        }
        return _ok('https://github.com/$repo/releases/tag/$tag\n');
      case ['repo', 'view', _, '--json', 'parent', '--jq', _]:
        return forkExists
            ? _ok('HelloHQ/plugin-registry\n')
            : _fail(1, 'Could not resolve to a Repository\n');
      case ['repo', 'clone', _, final dir]:
        Directory(dir).createSync(recursive: true);
        cloneDir = dir;
        return _ok();
      case ['pr', 'list', ...]:
        return _ok(prList);
      case ['pr', 'create', ...]:
        return _ok('https://github.com/HelloHQ/plugin-registry/pull/42\n');
    }
    return _ok();
  }

  ProcessResult _git(List<String> args, String? cwd) {
    switch (args) {
      case ['status', '--porcelain']:
        return _ok(gitStatus);
      case ['rev-parse', 'HEAD']:
        return _ok('$_headSha\n');
      case ['remote', 'get-url', 'origin']:
        return origin == null ? _fail(2, 'no such remote\n') : _ok('$origin\n');
      case ['remote', 'add', 'upstream', _]:
        return upstreamRemoteExists
            ? _fail(3, 'error: remote upstream already exists.\n')
            : _ok();
      case ['show', final spec] when spec.startsWith('upstream/main:'):
        return upstreamRaw == null
            ? _fail(128, 'fatal: path does not exist\n')
            : _ok(upstreamRaw!);
      case ['commit', ...]:
        return commitResult;
    }
    return _ok();
  }

  ProcessResult _node(List<String> args, String cwd) {
    switch (args) {
      case ['scripts/build-index.mjs']:
        writtenManifest = File(
          p.join(cwd, 'plugins', _id, 'manifest.json'),
        ).readAsStringSync();
        File(p.join(cwd, 'index.json')).writeAsStringSync('{"plugins":[]}\n');
        return _ok('Wrote index.json with 1 plugin(s)\n');
      case ['scripts/check-pr-signatures.mjs', final base, _]:
        baseManifestArg = base;
        if (base.isNotEmpty)
          baseManifestContent = File(base).readAsStringSync();
        return signatureCheck;
      case ['scripts/verify-artifacts.mjs', _]:
        return verifyArtifacts;
    }
    return _fail(1, 'unexpected node call');
  }
}

class Result {
  Result(this.code, this.out, this.err);
  final int code;
  final String out;
  final String err;
  @override
  String toString() => 'exit $code\n--- out\n$out\n--- err\n$err';
}

/// A temp plugin dir holding [manifest] and [files] (name → bytes).
Directory _pluginDir(
  Map<String, dynamic> manifest, [
  Map<String, List<int>> files = const {},
]) {
  final dir = Directory.systemTemp.createTempSync('hqplugin_publish_');
  addTearDown(() async {
    // Windows can hold a handle on a fresh temp dir briefly; retry.
    for (var attempt = 0; attempt < 12; attempt++) {
      try {
        dir.deleteSync(recursive: true);
        return;
      } catch (_) {
        await Future<void>.delayed(const Duration(milliseconds: 250));
      }
    }
  });
  File(
    p.join(dir.path, 'manifest.json'),
  ).writeAsStringSync(jsonEncode(manifest));
  files.forEach((name, bytes) {
    File(p.join(dir.path, name)).writeAsBytesSync(bytes);
  });
  return dir;
}

Future<Result> _publish(
  FakeWorld w,
  Directory dir, {
  String? version,
  String? bump,
  String? wasmPath,
  String? uiBundlePath,
  String? iconPath,
  bool release = false,
  String? repo,
  String? tag,
  bool allowDirty = false,
  bool submit = false,
  bool dryRun = false,
  bool firstParty = false,
  int maxArtifactBytes = kMaxArtifactBytes,
}) async {
  final out = StringBuffer();
  final err = StringBuffer();
  final code = await runPublish(
    version: version,
    bump: bump,
    wasmPath: wasmPath,
    uiBundlePath: uiBundlePath,
    iconPath: iconPath,
    release: release,
    repo: repo,
    tag: tag,
    allowDirty: allowDirty,
    submit: submit,
    dryRun: dryRun,
    firstParty: firstParty,
    out_: out,
    err_: err,
    commandRunner: w.run,
    httpGet: w.get,
    workingDirectory: dir.path,
    maxArtifactBytes: maxArtifactBytes,
    retryDelay: Duration.zero,
  );
  return Result(code, out.toString(), err.toString());
}

/// The registry manifest a plan printed (between its header and the next one).
Map<String, dynamic> _plannedManifest(String out) {
  final start = out.indexOf('── Registry manifest (');
  final json = out.substring(out.indexOf('\n', start) + 1);
  final end = json.indexOf('\n}\n');
  return jsonDecode(json.substring(0, end + 2)) as Map<String, dynamic>;
}

Map<String, dynamic> _written(FakeWorld w) =>
    jsonDecode(w.writtenManifest!) as Map<String, dynamic>;

// ─────────────────────────────────────────────────────────────────────────────

void main() {
  group('downloads and hashes the served bytes', () {
    test('pins the hash of what wasm_url serves, not the local file', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      // A different local plugin.wasm must not influence the pin.
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmB});
      final r = await _publish(w, dir);
      expect(r.code, 0, reason: '$r');
      final m = _plannedManifest(r.out);
      expect(m['content_hash_sha256'], sha256Hex(_wasmA));
      expect(m['content_hash_sha256'], isNot(sha256Hex(_wasmB)));
      expect(m['wasm_url'], _servedWasmUrl);
      expect(w.fetched, contains(_servedWasmUrl));
      // Without --release/--first-party nothing runs at all.
      expect(w.calls, isEmpty);
    });

    test('sha256Hex is SHA-256 in lowercase hex', () {
      expect(
        sha256Hex(utf8.encode('abc')),
        'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
      );
    });

    test('follows https redirects (GitHub release assets redirect)', () async {
      const cdn = 'https://objects.githubusercontent.com/asset/plugin.wasm';
      final w = FakeWorld()
        ..redirects[_servedWasmUrl] = cdn
        ..served[cdn] = _wasmA;
      final r = await _publish(w, _pluginDir(_manifest()));
      expect(r.code, 0, reason: '$r');
      expect(_plannedManifest(r.out)['content_hash_sha256'], sha256Hex(_wasmA));
      expect(w.fetched, containsAllInOrder([_servedWasmUrl, cdn]));
    });

    test(
      'warns when manifest.json pins a different hash than is served',
      () async {
        final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
        final dir = _pluginDir(
          _manifest({'content_hash_sha256': sha256Hex(_wasmB)}),
        );
        final r = await _publish(w, dir);
        expect(r.code, 0, reason: '$r');
        expect(r.err, contains('serves ${sha256Hex(_wasmA)}'));
        expect(
          _plannedManifest(r.out)['content_hash_sha256'],
          sha256Hex(_wasmA),
        );
      },
    );

    test('a download failure exits 69', () async {
      final r = await _publish(FakeWorld(), _pluginDir(_manifest()));
      expect(r.code, 69);
      expect(r.err, contains('HTTP 404'));
    });

    test('no wasm_url and no --release is refused', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest({'wasm_url': null})),
      );
      expect(r.code, 65);
      expect(r.err, contains('no wasm_url'));
    });
  });

  group('https only', () {
    test('an http wasm_url is refused before any download', () async {
      final w = FakeWorld();
      final dir = _pluginDir(
        _manifest({'wasm_url': 'http://example.com/plugin.wasm'}),
      );
      final r = await _publish(w, dir);
      expect(r.code, 65);
      expect(r.err, contains('not an https URL'));
      expect(w.fetched, isNot(contains('http://example.com/plugin.wasm')));
    });

    test('a redirect to http is refused', () async {
      final w = FakeWorld()
        ..redirects[_servedWasmUrl] = 'http://insecure.example/plugin.wasm';
      final r = await _publish(w, _pluginDir(_manifest()));
      expect(r.code, 65);
      expect(r.err, contains('non-https'));
    });

    test('downloadHttps refuses non-https schemes', () async {
      Future<HttpHop> never(Uri u, {required int maxBytes}) =>
          throw StateError('must not fetch');
      for (final url in ['http://x/y', 'file:///etc/passwd', 'ftp://x/y', '']) {
        await expectLater(
          downloadHttps(url, never),
          throwsA(
            isA<DownloadException>().having(
              (e) => e.refused,
              'refused',
              isTrue,
            ),
          ),
        );
      }
    });
  });

  group('size cap', () {
    test('matches the registry limit of 64 MiB', () {
      expect(kMaxArtifactBytes, 64 * 1024 * 1024);
    });

    test('an artifact over the cap is refused', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = List.filled(17, 0);
      final r = await _publish(
        w,
        _pluginDir(_manifest()),
        maxArtifactBytes: 16,
      );
      expect(r.code, 65);
      expect(r.err, contains('larger than 16 bytes'));
    });

    test('an artifact exactly at the cap is accepted', () async {
      final bytes = [..._wasmA, ...List.filled(7, 1)]; // 16 bytes
      final w = FakeWorld()..served[_servedWasmUrl] = bytes;
      final r = await _publish(
        w,
        _pluginDir(_manifest()),
        maxArtifactBytes: 16,
      );
      expect(r.code, 0, reason: '$r');
    });

    test(
      '--release refuses a local file over the cap before uploading',
      () async {
        final w = FakeWorld();
        final dir = _pluginDir(_manifest(), {
          'plugin.wasm': [..._wasmA, ...List.filled(20, 0)],
        });
        final r = await _publish(
          w,
          dir,
          release: true,
          submit: true,
          maxArtifactBytes: 16,
        );
        expect(r.code, 65);
        expect(r.err, contains('larger than 16 bytes'));
        expect(w.mutatingCalls, isEmpty);
      },
    );
  });

  group('magic number', () {
    test('a wasm plugin must serve a WebAssembly binary', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = utf8.encode('(module)');
      final r = await _publish(w, _pluginDir(_manifest()));
      expect(r.code, 65);
      expect(r.err, contains('missing the \\0asm magic number'));
    });

    test(
      'a sidecar plugin serves Python source, not checked for \\0asm',
      () async {
        const pyUrl =
            'https://github.com/$_authorRepo/releases/download/v1.0.0/plugin.py';
        final w = FakeWorld()..served[pyUrl] = _python;
        final dir = _pluginDir(
          _manifest({
            'execution_mode': 'sidecar',
            'min_host_version': '2.0.0',
            'wasm_url': pyUrl,
          }),
        );
        final r = await _publish(w, dir);
        expect(r.code, 0, reason: '$r');
        expect(
          _plannedManifest(r.out)['content_hash_sha256'],
          sha256Hex(_python),
        );
      },
    );

    test('a sidecar plugin serving a Wasm binary is refused', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final dir = _pluginDir(_manifest({'execution_mode': 'sidecar'}));
      final r = await _publish(w, dir);
      expect(r.code, 65);
      expect(r.err, contains('execution_mode is "sidecar"'));
    });

    test('pluginFileProblem rules', () {
      expect(pluginFileProblem(_wasmA, 'wasm'), isNull);
      expect(
        pluginFileProblem([0, 0x61, 0x73, 0x6d], 'wasm'),
        isNotNull,
        reason: 'shorter than the 8-byte header',
      );
      expect(pluginFileProblem(const [], 'wasm'), 'is empty');
      expect(pluginFileProblem(const [], 'sidecar'), 'is empty');
      expect(pluginFileProblem(_python, 'sidecar'), isNull);
      expect(pluginFileProblem(_wasmA, 'sidecar'), isNotNull);
    });
  });

  group('placeholder hash', () {
    test('registryManifestProblems refuses the all-zero placeholder', () {
      final m = _manifest(); // content_hash_sha256 is the placeholder
      expect(
        registryManifestProblems(m),
        contains(contains('all-zero placeholder')),
      );
      final ui = _manifest({
        'content_hash_sha256': 'a' * 64,
        'ui_bundle_url': 'https://x.example/ui.zip',
        'ui_bundle_hash_sha256': _placeholder,
      });
      expect(
        registryManifestProblems(ui),
        contains(contains('ui_bundle_hash_sha256 is the all-zero')),
      );
    });

    test('registryManifestProblems accepts a real manifest', () {
      expect(
        registryManifestProblems(_manifest({'content_hash_sha256': 'a' * 64})),
        isEmpty,
      );
    });

    test(
      'the placeholder in manifest.json is replaced by the served hash',
      () async {
        final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
        final r = await _publish(w, _pluginDir(_manifest()));
        expect(r.code, 0, reason: '$r');
        expect(r.out, isNot(contains(_placeholder)));
      },
    );

    test('artifactHashProblem refuses an empty artifact', () {
      expect(artifactHashProblem(const []), 'is empty');
      expect(artifactHashProblem(_uiZip), isNull);
    });
  });

  group('UI bundle URL and hash come together', () {
    const uiUrl =
        'https://github.com/$_authorRepo/releases/download/v1.0.0/ui.zip';

    test('ui_bundle_url is downloaded and both fields are pinned', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..served[uiUrl] = _uiZip;
      final dir = _pluginDir(
        _manifest({
          'ui_type': 'webview',
          'ui_bundle_url': uiUrl,
          'ui_bundle_hash_sha256': _placeholder,
        }),
      );
      final r = await _publish(w, dir);
      expect(r.code, 0, reason: '$r');
      final m = _plannedManifest(r.out);
      expect(m['ui_bundle_url'], uiUrl);
      expect(m['ui_bundle_hash_sha256'], sha256Hex(_uiZip));
    });

    test('a hash without a URL is refused', () async {
      final dir = _pluginDir(_manifest({'ui_bundle_hash_sha256': 'a' * 64}));
      final r = await _publish(FakeWorld(), dir);
      expect(r.code, 65);
      expect(r.err, contains('ui_bundle_hash_sha256 but no ui_bundle_url'));
    });

    test('webview without a UI bundle is refused', () async {
      final dir = _pluginDir(_manifest({'ui_type': 'webview'}));
      final r = await _publish(FakeWorld(), dir);
      expect(r.code, 65);
      expect(r.err, contains('needs a UI bundle'));
    });

    test('registryManifestProblems flags a half pair', () {
      expect(
        registryManifestProblems(
          _manifest({'content_hash_sha256': 'a' * 64, 'ui_bundle_url': uiUrl}),
        ),
        contains(contains('both be set or both be absent')),
      );
    });

    test('a non-webview manifest without a UI bundle drops both fields', () {
      final m = buildRegistryManifest(
        author: _manifest({'ui_bundle_hash_sha256': 'a' * 64}),
        upstream: null,
        version: '1.0.0',
        wasmUrl: _servedWasmUrl,
        contentHash: 'b' * 64,
      );
      expect(m.containsKey('ui_bundle_url'), isFalse);
      expect(m.containsKey('ui_bundle_hash_sha256'), isFalse);
    });
  });

  group('version', () {
    test('bumpVersion', () {
      expect(bumpVersion('1.2.3', 'patch'), '1.2.4');
      expect(bumpVersion('1.2.3', 'minor'), '1.3.0');
      expect(bumpVersion('1.2.3', 'major'), '2.0.0');
      expect(bumpVersion('0.0.9', 'patch'), '0.0.10');
      expect(bumpVersion('1.2.3+build.5', 'patch'), '1.2.4');
      // npm's prerelease rules.
      expect(bumpVersion('1.2.3-beta.1', 'patch'), '1.2.3');
      expect(bumpVersion('1.3.0-beta.1', 'minor'), '1.3.0');
      expect(bumpVersion('1.2.3-beta.1', 'minor'), '1.3.0');
      expect(bumpVersion('2.0.0-rc.1', 'major'), '2.0.0');
      expect(bumpVersion('1.2.0-rc.1', 'major'), '2.0.0');
      expect(() => bumpVersion('1.2', 'patch'), throwsFormatException);
      expect(() => bumpVersion('1.2.3', 'huge'), throwsFormatException);
    });

    test('Semver precedence', () {
      Semver v(String s) => Semver.tryParse(s)!;
      final ordered = [
        '1.0.0-alpha',
        '1.0.0-alpha.1',
        '1.0.0-alpha.beta',
        '1.0.0-beta',
        '1.0.0-beta.2',
        '1.0.0-beta.11',
        '1.0.0-rc.1',
        '1.0.0',
        '1.0.1',
        '1.1.0',
        '2.0.0',
      ];
      for (var i = 0; i + 1 < ordered.length; i++) {
        expect(
          v(ordered[i]).compareTo(v(ordered[i + 1])),
          -1,
          reason: '${ordered[i]} < ${ordered[i + 1]}',
        );
      }
      expect(v('1.0.0+a').compareTo(v('1.0.0+b')), 0);
      expect(Semver.tryParse('01.0.0'), isNull);
      expect(Semver.tryParse('1.0'), isNull);
    });

    test('--version and --bump are mutually exclusive', () async {
      final w = FakeWorld();
      final r = await _publish(
        w,
        _pluginDir(_manifest()),
        version: '1.0.1',
        bump: 'patch',
      );
      expect(r.code, 64);
      expect(r.err, contains('mutually exclusive'));
      expect(w.calls, isEmpty);
      expect(w.fetched, isEmpty);
    });

    test('an unknown --bump is a usage error', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest()),
        bump: 'huge',
      );
      expect(r.code, 64);
    });

    test('a malformed --version is a usage error', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest()),
        version: '1.2',
      );
      expect(r.code, 64);
    });

    test(
      '--bump is computed from manifest.json and names the release tag',
      () async {
        final w = FakeWorld();
        final dir = _pluginDir(_manifest({'version': '1.4.2'}), {
          'plugin.wasm': _wasmA,
        });
        final r = await _publish(w, dir, bump: 'minor', release: true);
        expect(r.code, 0, reason: '$r');
        expect(r.out, contains('version 1.4.2 → 1.5.0'));
        expect(
          w.lines,
          contains(startsWith('gh release view v1.5.0 --repo $_authorRepo')),
        );
        final m = _plannedManifest(r.out);
        expect(m['version'], '1.5.0');
        expect(m['wasm_url'], _releaseUrl('v1.5.0', 'plugin.wasm'));
      },
    );

    test('defaults to the manifest version', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final r = await _publish(w, _pluginDir(_manifest({'version': '3.1.4'})));
      expect(r.code, 0, reason: '$r');
      expect(_plannedManifest(r.out)['version'], '3.1.4');
    });

    test('a version below the registry\'s is refused', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({
          'version': '2.0.0',
          'content_hash_sha256': 'c' * 64,
        });
      final r = await _publish(w, _pluginDir(_manifest()));
      expect(r.code, 65);
      expect(r.err, contains('below the registry\'s 2.0.0'));
    });

    test('the same version with different content is refused', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({'content_hash_sha256': 'c' * 64});
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 65);
      expect(r.err, contains('higher version'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('the same version over a placeholder hash is allowed', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest();
      final r = await _publish(w, _pluginDir(_manifest()));
      expect(r.code, 0, reason: '$r');
    });

    test('re-running an identical publish is a no-op', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({'content_hash_sha256': sha256Hex(_wasmA)});
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(r.out, contains('already in the registry'));
      expect(w.mutatingCalls, isEmpty);
    });
  });

  group('--release', () {
    test(
      'creates the release, sets the URLs and pins the round-tripped bytes',
      () async {
        final w = FakeWorld();
        final dir = _pluginDir(
          _manifest({
            'version': '1.1.0',
            'ui_type': 'webview',
            'wasm_url': 'https://old.example/plugin.wasm',
          }),
          {'plugin.wasm': _wasmA, 'ui.zip': _uiZip},
        );
        final r = await _publish(w, dir, release: true, submit: true);
        expect(r.code, 0, reason: '$r');

        final create = w.calls.singleWhere(
          (c) => c.line.startsWith('gh release create'),
        );
        expect(create.args.sublist(0, 3), ['release', 'create', 'v1.1.0']);
        expect(
          create.args.map(p.basename),
          containsAll(['plugin.wasm', 'ui.zip']),
        );
        expect(
          create.args.join(' '),
          contains('--repo $_authorRepo --target $_headSha'),
        );

        final wasmUrl = _releaseUrl('v1.1.0', 'plugin.wasm');
        final uiUrl = _releaseUrl('v1.1.0', 'ui.zip');
        // Round trip: the pinned bytes are the ones downloaded after upload.
        expect(w.fetched, containsAll([wasmUrl, uiUrl]));
        final m = _written(w);
        expect(m['wasm_url'], wasmUrl);
        expect(m['content_hash_sha256'], sha256Hex(_wasmA));
        expect(m['ui_bundle_url'], uiUrl);
        expect(m['ui_bundle_hash_sha256'], sha256Hex(_uiZip));
        // The release is created before the registry is touched.
        final lines = w.lines.toList();
        expect(
          lines.indexWhere((l) => l.startsWith('gh release create')),
          lessThan(lines.indexWhere((l) => l.startsWith('gh repo clone'))),
        );
      },
    );

    test(
      'defaults to plugin.py for a sidecar and honours --tag/--repo',
      () async {
        final w = FakeWorld()..origin = null;
        final dir = _pluginDir(
          _manifest({'execution_mode': 'sidecar', 'min_host_version': '2.0.0'}),
          {'plugin.py': _python},
        );
        final r = await _publish(
          w,
          dir,
          release: true,
          submit: true,
          tag: 'summary-v1.0.0',
          repo: 'acme/mono',
        );
        expect(r.code, 0, reason: '$r');
        final url = _releaseUrl('summary-v1.0.0', 'plugin.py', 'acme/mono');
        expect(_written(w)['wasm_url'], url);
        expect(_written(w)['content_hash_sha256'], sha256Hex(_python));
        expect(w.lines, isNot(contains('git remote get-url origin')));
      },
    );

    test('--wasm and --ui-bundle pick other files', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest(), {
        'my_plugin.wasm': _wasmB,
        'plugin.wasm': _wasmA,
      });
      final r = await _publish(
        w,
        dir,
        release: true,
        submit: true,
        wasmPath: 'my_plugin.wasm',
      );
      expect(r.code, 0, reason: '$r');
      expect(_written(w)['wasm_url'], _releaseUrl('v1.0.0', 'my_plugin.wasm'));
      expect(_written(w)['content_hash_sha256'], sha256Hex(_wasmB));
    });

    test(
      'refuses when the uploaded asset does not serve the same bytes',
      () async {
        final w = FakeWorld()..uploadedOverride['plugin.wasm'] = _wasmB;
        final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
        final r = await _publish(w, dir, release: true, submit: true);
        expect(r.code, 65);
        expect(r.err, contains('serves different bytes'));
        expect(w.saw('gh repo clone'), isFalse);
      },
    );

    test('an existing release with identical assets is reused', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': false,
          'assets': [
            {'name': 'plugin.wasm'},
          ],
        }
        ..served[_releaseUrl('v1.0.0', 'plugin.wasm')] = _wasmA;
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(w.saw('gh release create'), isFalse);
      expect(w.saw('gh release upload'), isFalse);
      expect(_written(w)['content_hash_sha256'], sha256Hex(_wasmA));
      expect(r.out, contains('already exists'));
    });

    test('an existing release with different assets is refused', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': false,
          'assets': [
            {'name': 'plugin.wasm'},
          ],
        }
        ..served[_releaseUrl('v1.0.0', 'plugin.wasm')] = _wasmB;
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('immutable'));
      expect(r.err, contains('bump the version'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('an existing release missing the asset is refused', () async {
      final w = FakeWorld()..release = {'isDraft': false, 'assets': <Object>[]};
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('without "plugin.wasm"'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('a draft release is refused', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': true,
          'assets': [
            {'name': 'plugin.wasm'},
          ],
        };
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('draft'));
    });

    test('a dirty tree is refused with --submit', () async {
      final w = FakeWorld()..gitStatus = ' M src/lib.rs\n';
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('--allow-dirty'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('--allow-dirty overrides the clean-tree check', () async {
      final w = FakeWorld()..gitStatus = ' M src/lib.rs\n';
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(
        w,
        dir,
        release: true,
        submit: true,
        allowDirty: true,
      );
      expect(r.code, 0, reason: '$r');
      expect(w.saw('gh release create'), isTrue);
    });

    test('a missing local file exits 66', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest()),
        release: true,
      );
      expect(r.code, 66);
      expect(r.err, contains('Run `hqplugin build` first'));
    });

    test('a local non-Wasm file is refused before any release call', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest(), {
        'plugin.wasm': utf8.encode('not wasm'),
      });
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(w.mutatingCalls, isEmpty);
    });

    test('the same version as the registry cannot get a new release', () async {
      final w = FakeWorld()
        ..upstream = _manifest({'content_hash_sha256': 'c' * 64});
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('--bump patch'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('release-only flags without --release are usage errors', () async {
      for (final flags in <Future<Result> Function(FakeWorld, Directory)>[
        (w, d) => _publish(w, d, repo: 'a/b'),
        (w, d) => _publish(w, d, tag: 'v1'),
        (w, d) => _publish(w, d, wasmPath: 'plugin.wasm'),
        (w, d) => _publish(w, d, uiBundlePath: 'ui.zip'),
        (w, d) => _publish(w, d, allowDirty: true),
      ]) {
        final r = await flags(FakeWorld(), _pluginDir(_manifest()));
        expect(r.code, 64, reason: '$r');
        expect(r.err, contains('only apply with --release'));
      }
    });

    test('parseGitHubRepo', () {
      expect(parseGitHubRepo('https://github.com/o/n.git'), 'o/n');
      expect(parseGitHubRepo('https://github.com/o/n'), 'o/n');
      expect(parseGitHubRepo('https://token@github.com/o/n.git'), 'o/n');
      expect(parseGitHubRepo('git@github.com:o/my.repo.git'), 'o/my.repo');
      expect(parseGitHubRepo('ssh://git@github.com/o/n.git'), 'o/n');
      expect(parseGitHubRepo('https://gitlab.com/o/n.git'), isNull);
    });

    test('the repo is inferred from origin', () async {
      final w = FakeWorld()..origin = 'https://github.com/someone/thing.git';
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true);
      expect(r.code, 0, reason: '$r');
      expect(
        w.lines,
        contains(startsWith('gh release view v1.0.0 --repo someone/thing')),
      );
    });

    test('a non-GitHub origin needs --repo', () async {
      final w = FakeWorld()..origin = 'https://gitlab.com/o/n.git';
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true);
      expect(r.code, 64);
      expect(r.err, contains('--repo'));
    });
  });

  group('registry manifest rules', () {
    final signatures = [
      {'key_id': 'k1', 'alg': 'ed25519', 'sig': 'AAAA'},
    ];

    test('signatures are always stripped (new plugin)', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final dir = _pluginDir(_manifest({'signatures': signatures}));
      final r = await _publish(w, dir, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(_written(w).containsKey('signatures'), isFalse);
    });

    test('signatures are always stripped (update)', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({
          'version': '0.9.0',
          'content_hash_sha256': 'c' * 64,
          'signatures': signatures,
        });
      final dir = _pluginDir(_manifest({'signatures': signatures}));
      final r = await _publish(w, dir, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(_written(w).containsKey('signatures'), isFalse);
    });

    test('trust_tier is copied from the registry on an update', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({
          'version': '0.9.0',
          'content_hash_sha256': 'c' * 64,
          'trust_tier': 'verified',
          'publisher_signing_key_id': 'key-1',
        });
      final dir = _pluginDir(
        _manifest({
          'trust_tier': 'official', // self-assigned: ignored
          'publisher_signing_key_id': 'mine',
        }),
      );
      final r = await _publish(w, dir, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(_written(w)['trust_tier'], 'verified');
      expect(_written(w)['publisher_signing_key_id'], 'key-1');
    });

    test(
      'trust_tier is removed on an update whose registry copy has none',
      () async {
        final w = FakeWorld()
          ..served[_servedWasmUrl] = _wasmA
          ..upstream = _manifest({
            'version': '0.9.0',
            'content_hash_sha256': 'c' * 64,
          });
        final dir = _pluginDir(_manifest({'trust_tier': 'verified'}));
        final r = await _publish(w, dir);
        expect(r.code, 0, reason: '$r');
        expect(_plannedManifest(r.out).containsKey('trust_tier'), isFalse);
      },
    );

    test('trust_tier is removed for a new plugin', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final dir = _pluginDir(_manifest({'trust_tier': 'verified'}));
      final r = await _publish(w, dir, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(_written(w).containsKey('trust_tier'), isFalse);
    });

    test(
      '--first-party keeps the manifest trust_tier and allows core',
      () async {
        final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
        final dir = _pluginDir(
          _manifest({
            'trust_tier': 'official',
            'provenance': 'core',
            'signatures': signatures,
          }),
        );
        final r = await _publish(w, dir, submit: true, firstParty: true);
        expect(r.code, 0, reason: '$r');
        expect(_written(w)['trust_tier'], 'official');
        expect(_written(w)['provenance'], 'core');
        expect(_written(w).containsKey('signatures'), isFalse);
        expect(
          w.lines,
          contains('gh api orgs/HelloHQ/members/octocat --silent'),
        );
      },
    );

    test('--first-party is refused for a non-member', () async {
      final w = FakeWorld()
        ..member = false
        ..served[_servedWasmUrl] = _wasmA;
      final dir = _pluginDir(
        _manifest({'trust_tier': 'official', 'provenance': 'core'}),
      );
      final r = await _publish(w, dir, submit: true, firstParty: true);
      expect(r.code, 77);
      expect(r.err, contains('not a member of the HelloHQ'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('--first-party needs gh', () async {
      final w = FakeWorld()..hasGh = false;
      final dir = _pluginDir(_manifest({'provenance': 'core'}));
      final r = await _publish(w, dir, firstParty: true);
      expect(r.code, 69);
      expect(r.err, contains('GitHub CLI'));
    });

    test('the registry file is 2-space JSON with a trailing newline', () {
      expect(
        encodeManifest({
          'a': 1,
          'b': [1],
        }),
        '{\n  "a": 1,\n  "b": [\n    1\n  ]\n}\n',
      );
    });

    test('sameRegistryContent ignores key order and signatures', () {
      expect(
        sameRegistryContent(
          {'a': 1, 'b': 2},
          {'b': 2, 'a': 1, 'signatures': []},
        ),
        isTrue,
      );
      expect(sameRegistryContent({'a': 1}, {'a': 2}), isFalse);
    });
  });

  // These reject before any artifact download or process call.
  group('provenance/licensing gates', () {
    Future<(Result, FakeWorld)> gated(
      Map<String, dynamic> overrides, {
      bool firstParty = false,
    }) async {
      final w = FakeWorld();
      final r = await _publish(
        w,
        _pluginDir(_manifest(overrides)),
        submit: true,
        firstParty: firstParty,
      );
      return (r, w);
    }

    test('rejects enterprise provenance', () async {
      final (r, w) = await gated({'provenance': 'enterprise'});
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('enterprise'));
      expect(w.calls, isEmpty);
      expect(w.fetched, isEmpty);
    });

    test('rejects enterprise provenance even with --first-party', () async {
      final (r, w) = await gated({
        'provenance': 'enterprise',
      }, firstParty: true);
      expect(r.code, 65);
      expect(w.calls, isEmpty);
    });

    test('rejects core provenance without --first-party', () async {
      final (r, w) = await gated({'provenance': 'core'});
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('core'));
      expect(r.err, contains('--first-party'));
      expect(w.calls, isEmpty);
      expect(w.fetched, isEmpty);
    });

    test('rejects commercial licensing', () async {
      final (r, w) = await gated({
        'licensing': {'kind': 'commercial', 'product_id': 'sku_x'},
      });
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('commercial'));
      expect(w.calls, isEmpty);
    });

    test('rejects commercial licensing even with --first-party', () async {
      final (r, _) = await gated({
        'licensing': {'kind': 'commercial', 'product_id': 'sku_x'},
      }, firstParty: true);
      expect(r.code, 65);
    });

    test('warns (but does not fail) on open-source without spdx', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final r = await _publish(
        w,
        _pluginDir(
          _manifest({
            'licensing': {'kind': 'open_source'},
          }),
        ),
      );
      expect(r.code, 0, reason: '$r');
      expect(r.err.toLowerCase(), contains('spdx'));
    });
  });

  group('--submit: the registry PR', () {
    FakeWorld world() => FakeWorld()..served[_servedWasmUrl] = _wasmA;

    test('runs the registry scripts in the clone and adds index.json', () async {
      final w = world();
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      final clone = w.cloneDir!;
      final node = w.calls.where(
        (c) => c.exe == 'node' && c.args.first != '--version',
      );
      expect(node.map((c) => c.args), [
        ['scripts/build-index.mjs'],
        ['scripts/check-pr-signatures.mjs', '', 'plugins/$_id/manifest.json'],
        ['scripts/verify-artifacts.mjs', 'plugins/$_id/manifest.json'],
      ]);
      expect(node.every((c) => c.cwd == clone), isTrue);
      expect(
        w.lines,
        contains('git add plugins/$_id/manifest.json index.json'),
      );
      // The manifest file is the registry format.
      expect(w.writtenManifest, endsWith('}\n'));
      expect(w.writtenManifest, contains('\n  "id": "$_id",\n'));
      expect(
        w.lines,
        contains('git checkout -B publish/$_id/1.0.0 upstream/main'),
      );
      expect(
        w.lines,
        contains('git push --force -u origin publish/$_id/1.0.0'),
      );
      final create = w.calls.singleWhere(
        (c) => c.line.startsWith('gh pr create'),
      );
      expect(
        create.args.join(' '),
        contains(
          '--repo HelloHQ/plugin-registry --head octocat:publish/$_id/1.0.0 --base main',
        ),
      );
      expect(
        create.args[create.args.indexOf('--title') + 1],
        'Add plugin: $_id 1.0.0',
      );
      expect(r.out, contains('pull/42'));
      // The commit carries the same title.
      expect(w.lines, contains('git commit -m Add plugin: $_id 1.0.0'));
    });

    test('an update passes the base manifest to check-pr-signatures', () async {
      final upstream = _manifest({
        'version': '0.9.0',
        'content_hash_sha256': 'c' * 64,
      });
      final w = world()..upstream = upstream;
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(w.baseManifestArg, isNotEmpty);
      expect(jsonDecode(w.baseManifestContent!), upstream);
      final create = w.calls.singleWhere(
        (c) => c.line.startsWith('gh pr create'),
      );
      expect(
        create.args[create.args.indexOf('--title') + 1],
        'Update plugin: $_id 0.9.0 → 1.0.0',
      );
    });

    test('creates the fork when the user has none', () async {
      final w = world()..forkExists = false;
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(
        w.lines,
        contains('gh repo fork HelloHQ/plugin-registry --clone=false'),
      );
    });

    test('refuses a same-named repo that is not a registry fork', () async {
      final w2 = world();
      final dir = _pluginDir(_manifest());
      // Override the fork lookup to name another parent.
      Future<ProcessResult> runner(
        String exe,
        List<String> args, {
        String? workingDirectory,
      }) async {
        if (exe == 'gh' &&
            args.length > 2 &&
            args[0] == 'repo' &&
            args[1] == 'view') {
          w2.calls.add(Call(exe, args, workingDirectory));
          return _ok('someone/else\n');
        }
        return w2.run(exe, args, workingDirectory: workingDirectory);
      }

      final err = StringBuffer();
      final code = await runPublish(
        submit: true,
        out_: StringBuffer(),
        err_: err,
        commandRunner: runner,
        httpGet: w2.get,
        workingDirectory: dir.path,
      );
      expect(code, 65);
      expect(err.toString(), contains('not a fork'));
      expect(w2.saw('git push'), isFalse);
    });

    test('reuses an upstream remote that gh clone already added', () async {
      final w = world()..upstreamRemoteExists = true;
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(
        w.lines,
        contains(
          'git remote set-url upstream https://github.com/HelloHQ/plugin-registry.git',
        ),
      );
    });

    test('a verify-artifacts failure aborts before the push', () async {
      final w = world()
        ..verifyArtifacts = ProcessResult(
          1,
          1,
          '::error file=plugins/$_id/manifest.json::wasm_url SHA-256 mismatch: want a got b\n',
          '',
        );
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 65);
      expect(r.err, contains('SHA-256 mismatch'));
      expect(r.err, contains('nothing was pushed'));
      expect(w.saw('git push'), isFalse);
      expect(w.saw('gh pr create'), isFalse);
    });

    test('missing wasm-tools in verify-artifacts is only a warning', () async {
      final w = world()
        ..verifyArtifacts = ProcessResult(
          1,
          1,
          '  wasm_url SHA-256 ${'a' * 64} (9 bytes) ✅\n'
              '::error file=plugins/$_id/manifest.json::wasm_url is not valid WebAssembly: could not run wasm-tools (spawnSync wasm-tools ENOENT)\n',
          '',
        );
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(r.err, contains('wasm-tools is not on PATH'));
      expect(r.err, contains('Registry CI runs it'));
      expect(w.saw('gh pr create'), isTrue);
    });

    test('a check-pr-signatures refusal aborts before the push', () async {
      final w = world()
        ..signatureCheck = ProcessResult(
          1,
          1,
          'a new plugin must not carry signatures\n',
          '',
        );
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 65);
      expect(r.err, contains('must not carry signatures'));
      expect(w.saw('git push'), isFalse);
    });

    test('missing node is refused before any mutation', () async {
      final w = world()..hasNode = false;
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 69);
      expect(r.err, contains('requires Node.js'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('missing gh is refused for --submit', () async {
      final w = world()..hasGh = false;
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 69);
      expect(r.err, contains('GitHub CLI'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('an open PR from the same branch is updated, not duplicated', () async {
      final w = world()
        ..prList = jsonEncode([
          {
            'url': 'https://github.com/HelloHQ/plugin-registry/pull/7',
            'headRepositoryOwner': {'login': 'octocat'},
          },
        ]);
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      expect(w.saw('gh pr create'), isFalse);
      expect(w.saw('git push --force'), isTrue);
      expect(r.out, contains('pull/7'));
      expect(
        w.lines,
        contains(
          'gh pr list --repo HelloHQ/plugin-registry --head publish/$_id/1.0.0 '
          '--state open --json url,headRepositoryOwner',
        ),
      );
    });

    test(
      'an open PR from another fork with the same branch is ignored',
      () async {
        final w = world()
          ..prList = jsonEncode([
            {
              'url': 'https://github.com/HelloHQ/plugin-registry/pull/8',
              'headRepositoryOwner': {'login': 'someone-else'},
            },
          ]);
        final r = await _publish(w, _pluginDir(_manifest()), submit: true);
        expect(r.code, 0, reason: '$r');
        expect(w.saw('gh pr create'), isTrue);
      },
    );

    test('stops when git commit fails', () async {
      final w = world()..commitResult = _fail(128, 'commit failed\n');
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 128);
      expect(r.err, contains('git commit failed'));
      expect(w.saw('git push'), isFalse);
      expect(w.saw('gh pr create'), isFalse);
    });

    test('the clone\'s registry state wins over the planning read', () async {
      // The raw read says "new"; the clone shows the plugin exists with a
      // trust tier, so the PR is an update that carries it.
      final upstream = _manifest({
        'version': '0.9.0',
        'content_hash_sha256': 'c' * 64,
        'trust_tier': 'verified',
      });
      final w = world();
      Future<HttpHop> get(Uri u, {required int maxBytes}) =>
          u.toString() == _rawUpstream
          ? Future.value(const HttpHop(404))
          : w.get(u, maxBytes: maxBytes);
      w.upstream = upstream;
      final dir = _pluginDir(_manifest());
      final out = StringBuffer();
      final code = await runPublish(
        submit: true,
        out_: out,
        err_: StringBuffer(),
        commandRunner: w.run,
        httpGet: get,
        workingDirectory: dir.path,
      );
      expect(code, 0, reason: '$out');
      expect(out.toString(), contains('registry changed since the plan'));
      expect(_written(w)['trust_tier'], 'verified');
      final create = w.calls.singleWhere(
        (c) => c.line.startsWith('gh pr create'),
      );
      expect(
        create.args[create.args.indexOf('--title') + 1],
        'Update plugin: $_id 0.9.0 → 1.0.0',
      );
    });
  });

  group('sidebar icon', () {
    test('pins the hash of what an https sidebar_icon serves', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..served[_servedIconUrl] = _iconA;
      // A different local icon.svg must not influence the pin.
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'icon.svg': _iconB,
      });
      final r = await _publish(w, dir);
      expect(r.code, 0, reason: '$r');
      final m = _plannedManifest(r.out);
      expect(m['sidebar_icon'], _servedIconUrl);
      expect(m['sidebar_icon_hash_sha256'], sha256Hex(_iconA));
      expect(w.fetched, contains(_servedIconUrl));
      expect(r.out, contains('`sidebar_icon_hash_sha256`'));
    });

    test('the icon cap is 64 KiB, independent of the artifact cap', () async {
      final big = [..._iconA, ...List.filled(kMaxIconBytes, 0x20)];
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..served[_servedIconUrl] = big;
      final r = await _publish(
        w,
        _pluginDir(_manifest({'sidebar_icon': _servedIconUrl})),
      );
      expect(r.code, 65);
      expect(r.err, contains('larger than 65536 bytes'));
    });

    test('an unsafe served icon is refused before the PR', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..served[_servedIconUrl] = utf8.encode(
          '<svg><script>alert(1)</script></svg>',
        );
      final r = await _publish(
        w,
        _pluginDir(_manifest({'sidebar_icon': _servedIconUrl})),
        submit: true,
      );
      expect(r.code, 65);
      expect(r.err, contains('sidebar_icon $_servedIconUrl contains <script>'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('a non-https absolute icon is refused before any download', () async {
      for (final url in [
        'http://example.com/icon.svg',
        'data:image/svg+xml,<svg/>',
        'file:///etc/icon.svg',
      ]) {
        final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
        final r = await _publish(
          w,
          _pluginDir(_manifest({'sidebar_icon': url})),
        );
        expect(r.code, 65, reason: url);
        expect(r.err, contains('must be an https URL'));
        expect(w.fetched, isEmpty, reason: url);
      }
    });

    test(
      'a bundle-path icon gets no hash and a stale one is dropped',
      () async {
        const uiUrl =
            'https://github.com/$_authorRepo/releases/download/v1.0.0/ui.zip';
        final w = FakeWorld()
          ..served[_servedWasmUrl] = _wasmA
          ..served[uiUrl] = _uiZip;
        final dir = _pluginDir(
          _manifest({
            'ui_type': 'webview',
            'ui_bundle_url': uiUrl,
            'sidebar_icon': 'icons/plugin.svg',
            'sidebar_icon_hash_sha256': 'c' * 64,
          }),
        );
        final r = await _publish(w, dir);
        expect(r.code, 0, reason: '$r');
        final m = _plannedManifest(r.out);
        expect(m['sidebar_icon'], 'icons/plugin.svg');
        expect(m.containsKey('sidebar_icon_hash_sha256'), isFalse);
        expect(r.err, contains('dropping sidebar_icon_hash_sha256'));
        expect(w.fetched, isNot(contains(contains('icons/plugin.svg'))));
      },
    );

    test('a bundle-path icon on a non-webview plugin warns', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final r = await _publish(
        w,
        _pluginDir(_manifest({'sidebar_icon': 'icon.svg'})),
      );
      expect(r.code, 0, reason: '$r');
      expect(r.err, contains('generic icon'));
    });

    test('a stale hash without any sidebar_icon is dropped', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final r = await _publish(
        w,
        _pluginDir(_manifest({'sidebar_icon_hash_sha256': 'c' * 64})),
      );
      expect(r.code, 0, reason: '$r');
      expect(
        _plannedManifest(r.out).containsKey('sidebar_icon_hash_sha256'),
        isFalse,
      );
    });

    test('--icon without --release is a usage error', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest()),
        iconPath: 'icon.svg',
      );
      expect(r.code, 64);
      expect(r.err, contains('--icon'));
    });

    test('--release uploads ./icon.svg and repoints sidebar_icon', () async {
      final w = FakeWorld();
      final dir = _pluginDir(
        _manifest({
          'version': '1.0.1',
          // Still the previous release's icon: publish repoints it.
          'sidebar_icon': _servedIconUrl,
        }),
        {'plugin.wasm': _wasmA, 'icon.svg': _iconA},
      );
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 0, reason: '$r');
      final create = w.calls.singleWhere(
        (c) => c.line.startsWith('gh release create'),
      );
      expect(
        create.args.map(p.basename),
        containsAll(['plugin.wasm', 'icon.svg']),
      );
      final iconUrl = _releaseUrl('v1.0.1', 'icon.svg');
      expect(w.fetched, contains(iconUrl), reason: 'round trip');
      final m = _written(w);
      expect(m['sidebar_icon'], iconUrl);
      expect(m['sidebar_icon_hash_sha256'], sha256Hex(_iconA));
      expect(w.fetched, isNot(contains(_servedIconUrl)));
    });

    test('--release refuses an icon that does not round-trip', () async {
      final w = FakeWorld()..uploadedOverride['icon.svg'] = _iconB;
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('serves different bytes'));
      expect(w.saw('gh repo clone'), isFalse);
    });

    test('--release --icon picks another file', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'brand.svg': _iconB,
        'icon.svg': _iconA,
      });
      final r = await _publish(
        w,
        dir,
        release: true,
        submit: true,
        iconPath: 'brand.svg',
      );
      expect(r.code, 0, reason: '$r');
      expect(_written(w)['sidebar_icon'], _releaseUrl('v1.0.0', 'brand.svg'));
      expect(_written(w)['sidebar_icon_hash_sha256'], sha256Hex(_iconB));
    });

    test('--release --icon adds an icon to a manifest without one', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest(), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(
        w,
        dir,
        release: true,
        submit: true,
        iconPath: 'icon.svg',
      );
      expect(r.code, 0, reason: '$r');
      expect(_written(w)['sidebar_icon'], _releaseUrl('v1.0.0', 'icon.svg'));
      expect(_written(w)['sidebar_icon_hash_sha256'], sha256Hex(_iconA));
    });

    test(
      '--release never adds an icon the manifest does not declare',
      () async {
        final w = FakeWorld();
        final dir = _pluginDir(_manifest(), {
          'plugin.wasm': _wasmA,
          'icon.svg': _iconA,
        });
        final r = await _publish(w, dir, release: true, submit: true);
        expect(r.code, 0, reason: '$r');
        final create = w.calls.singleWhere(
          (c) => c.line.startsWith('gh release create'),
        );
        expect(create.args.map(p.basename), isNot(contains('icon.svg')));
        expect(_written(w).containsKey('sidebar_icon'), isFalse);
        expect(r.out, contains('Pass --icon icon.svg'));
      },
    );

    test(
      '--release without a local icon pins the existing URL and warns',
      () async {
        final w = FakeWorld()..served[_servedIconUrl] = _iconA;
        final dir = _pluginDir(
          _manifest({'version': '1.0.1', 'sidebar_icon': _servedIconUrl}),
          {'plugin.wasm': _wasmA},
        );
        final r = await _publish(w, dir, release: true, submit: true);
        expect(r.code, 0, reason: '$r');
        expect(_written(w)['sidebar_icon'], _servedIconUrl);
        expect(_written(w)['sidebar_icon_hash_sha256'], sha256Hex(_iconA));
        expect(r.err, contains('still points at another release'));
      },
    );

    test('--release --icon with a missing file exits 66', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
          'plugin.wasm': _wasmA,
        }),
        release: true,
        iconPath: 'nope.svg',
      );
      expect(r.code, 66);
      expect(r.err, contains('--icon <path>'));
    });

    test('--release --icon conflicts with a bundle-path icon', () async {
      final r = await _publish(
        FakeWorld(),
        _pluginDir(_manifest({'sidebar_icon': 'icons/plugin.svg'}), {
          'plugin.wasm': _wasmA,
          'icon.svg': _iconA,
        }),
        release: true,
        iconPath: 'icon.svg',
      );
      expect(r.code, 64);
      expect(r.err, contains('path inside the UI bundle'));
    });

    test(
      '--release refuses an unsafe or oversized local icon up front',
      () async {
        for (final bad in [
          utf8.encode('<svg onload="x()"/>'),
          [..._iconA, ...List.filled(kMaxIconBytes, 0x20)],
        ]) {
          final w = FakeWorld();
          final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
            'plugin.wasm': _wasmA,
            'icon.svg': bad,
          });
          final r = await _publish(w, dir, release: true, submit: true);
          expect(r.code, 65, reason: '$r');
          expect(w.mutatingCalls, isEmpty);
        }
      },
    );

    test('an existing release reuses an identical icon', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': false,
          'assets': [
            {'name': 'plugin.wasm'},
            {'name': 'icon.svg'},
          ],
        }
        ..served[_releaseUrl('v1.0.0', 'plugin.wasm')] = _wasmA
        ..served[_releaseUrl('v1.0.0', 'icon.svg')] = _iconA;
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 0, reason: '$r');
      expect(w.saw('gh release create'), isFalse);
      expect(_written(w)['sidebar_icon_hash_sha256'], sha256Hex(_iconA));
    });

    test('an existing release with a different icon is refused', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': false,
          'assets': [
            {'name': 'plugin.wasm'},
            {'name': 'icon.svg'},
          ],
        }
        ..served[_releaseUrl('v1.0.0', 'plugin.wasm')] = _wasmA
        ..served[_releaseUrl('v1.0.0', 'icon.svg')] = _iconB;
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('immutable'));
      expect(w.mutatingCalls, isEmpty);
    });

    test('an existing release without the icon asset is refused', () async {
      final w = FakeWorld()
        ..release = {
          'isDraft': false,
          'assets': [
            {'name': 'plugin.wasm'},
          ],
        };
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(w, dir, release: true, submit: true);
      expect(r.code, 65);
      expect(r.err, contains('without "icon.svg"'));
    });

    test('the dry-run plan shows the icon as provisional', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest({'sidebar_icon': _servedIconUrl}), {
        'plugin.wasm': _wasmA,
        'icon.svg': _iconA,
      });
      final r = await _publish(w, dir, release: true, dryRun: true);
      expect(r.code, 0, reason: '$r');
      expect(w.mutatingCalls, isEmpty);
      expect(r.out, contains('with plugin.wasm, icon.svg'));
      expect(
        _plannedManifest(r.out)['sidebar_icon'],
        _releaseUrl('v1.0.0', 'icon.svg'),
      );
    });
  });

  group('dry run', () {
    test(
      '--dry-run makes no mutating call even with --release --submit',
      () async {
        final w = FakeWorld();
        final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
        final r = await _publish(
          w,
          dir,
          release: true,
          submit: true,
          dryRun: true,
        );
        expect(r.code, 0, reason: '$r');
        expect(w.mutatingCalls, isEmpty);
        for (final prefix in [
          'gh release create',
          'gh repo fork',
          'git push',
          'gh pr create',
        ]) {
          expect(w.saw(prefix), isFalse, reason: prefix);
        }
        // Reads are allowed: the release lookup happened.
        expect(w.saw('gh release view v1.0.0'), isTrue);
        expect(r.out, contains('would be created at $_headSha'));
        expect(r.out, contains('hash of the local file'));
        expect(r.out, contains('plan only'));
        expect(r.out, contains('Add plugin: $_id 1.0.0'));
      },
    );

    test('without --submit nothing is mutated either', () async {
      final w = FakeWorld();
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true);
      expect(r.code, 0, reason: '$r');
      expect(w.mutatingCalls, isEmpty);
    });

    test('a dirty tree is only a warning in a dry run', () async {
      final w = FakeWorld()..gitStatus = '?? notes.txt\n';
      final dir = _pluginDir(_manifest(), {'plugin.wasm': _wasmA});
      final r = await _publish(w, dir, release: true, dryRun: true);
      expect(r.code, 0, reason: '$r');
      expect(r.err, contains('--submit will refuse'));
    });

    test('downloads, hashes and diffs against the registry', () async {
      final w = FakeWorld()
        ..served[_servedWasmUrl] = _wasmA
        ..upstream = _manifest({
          'version': '0.9.0',
          'content_hash_sha256': 'c' * 64,
          'trust_tier': 'verified',
        });
      final r = await _publish(w, _pluginDir(_manifest()), dryRun: true);
      expect(r.code, 0, reason: '$r');
      expect(w.calls, isEmpty);
      expect(w.fetched, containsAll([_rawUpstream, _servedWasmUrl]));
      expect(r.out, contains('update of 0.9.0'));
      expect(r.out, contains('- ${'  "version": "0.9.0",'}'));
      expect(r.out, contains('+ ${'  "version": "1.0.0",'}'));
      expect(
        r.out,
        contains('+   "content_hash_sha256": "${sha256Hex(_wasmA)}",'),
      );
      expect(r.out, contains('Update plugin: $_id 0.9.0 → 1.0.0'));
    });

    test('lineDiff', () {
      expect(lineDiff('a\nb\nc\n', 'a\nx\nc\n'), ['  a', '- b', '+ x', '  c']);
      expect(lineDiff('', 'a\n'), ['+ a']);
    });
  });

  group('PR title and body (golden)', () {
    const wasmUrl =
        'https://github.com/octocat/summary/releases/download/v1.1.0/plugin.wasm';
    const uiUrl =
        'https://github.com/octocat/summary/releases/download/v1.1.0/ui.zip';
    final h1 = 'a' * 64;
    final h2 = 'b' * 64;

    test('new plugin', () {
      final m = <String, dynamic>{
        'id': _id,
        'version': '1.0.0',
        'wasm_url': wasmUrl,
        'content_hash_sha256': h1,
        'licensing': {'kind': 'open_source', 'spdx': 'MIT'},
        'permissions': [
          {'id': 'read:portfolio_names'},
          {
            'id': 'network:fetch',
            'scope': {
              'origins': ['https://a.example'],
            },
          },
          {'id': 'propose:holdings'},
        ],
      };
      expect(
        registryPrTitle(id: _id, version: '1.0.0', upstreamVersion: null),
        'Add plugin: $_id 1.0.0',
      );
      expect(
        registryPrBody(
          manifest: m,
          upstream: null,
          artifactSizes: {'wasm_url': 1234},
        ),
        '''
**New plugin** `$_id` 1.0.0.

Opened by `hqplugin publish`.

### Artifacts

Each hash is the SHA-256 of the bytes downloaded from its URL.

- `wasm_url`: $wasmUrl
  - `content_hash_sha256`: `$h1` (1234 bytes)

### Permissions

- `read:portfolio_names`
- `network:fetch` — scope `{"origins":["https://a.example"]}`
- `propose:holdings`

### Trust tier

`trust_tier` is not set; the registry team assigns it.

This plugin declares Verified-only capabilities (`propose:holdings`). The registry team reviews them and assigns the tier; CI fails until `trust_tier` is `verified`.

### Provenance and licensing

- provenance: `community`
- licensing: `open_source` (`MIT`)

`signatures` are left out; the signing pipeline signs the merged manifest.
''',
      );
    });

    test('update with a permission delta', () {
      final upstream = <String, dynamic>{
        'id': _id,
        'version': '1.0.0',
        'trust_tier': 'verified',
        'permissions': [
          {'id': 'read:portfolio_names'},
          {
            'id': 'network:fetch',
            'scope': {
              'origins': ['https://a.example'],
            },
          },
          {'id': 'read:currencies'},
        ],
      };
      final m = <String, dynamic>{
        'id': _id,
        'version': '1.1.0',
        'execution_mode': 'sidecar',
        'wasm_url': wasmUrl,
        'content_hash_sha256': h1,
        'ui_type': 'webview',
        'ui_bundle_url': uiUrl,
        'ui_bundle_hash_sha256': h2,
        'trust_tier': 'verified',
        'licensing': {'kind': 'open_source', 'spdx': 'Apache-2.0'},
        'permissions': [
          {'id': 'read:portfolio_names'},
          {
            'id': 'network:fetch',
            'scope': {
              'origins': ['https://a.example', 'https://b.example'],
            },
          },
          {'id': 'ai:inference'},
        ],
      };
      expect(
        registryPrTitle(id: _id, version: '1.1.0', upstreamVersion: '1.0.0'),
        'Update plugin: $_id 1.0.0 → 1.1.0',
      );
      expect(
        registryPrBody(
          manifest: m,
          upstream: upstream,
          artifactSizes: {'wasm_url': 1234, 'ui_bundle_url': 99},
        ),
        '''
**Update** of `$_id`: 1.0.0 → 1.1.0.

Opened by `hqplugin publish`.

### Artifacts

Each hash is the SHA-256 of the bytes downloaded from its URL.

- `wasm_url`: $wasmUrl
  - `content_hash_sha256`: `$h1` (1234 bytes)
- `ui_bundle_url`: $uiUrl
  - `ui_bundle_hash_sha256`: `$h2` (99 bytes)

### Permissions

- Added: `ai:inference`
- Removed: `read:currencies`
- Scope changed: `network:fetch`: `{"origins":["https://a.example"]}` → `{"origins":["https://a.example","https://b.example"]}`

### Trust tier

`trust_tier` (`verified`) is carried over from the registry; this PR does not change it.

This plugin declares Verified-only capabilities (`execution_mode: sidecar`, `ai:inference`, `ui_type: webview`); its `trust_tier` (`verified`) allows them.

### Provenance and licensing

- provenance: `community`
- licensing: `open_source` (`Apache-2.0`)

`signatures` are left out; the signing pipeline signs the merged manifest.
''',
      );
    });

    test('update without permission changes, first-party', () {
      final m = <String, dynamic>{
        'id': _id,
        'version': '1.0.1',
        'wasm_url': wasmUrl,
        'content_hash_sha256': h1,
        'trust_tier': 'official',
        'provenance': 'core',
        'permissions': [
          {'id': 'read:portfolio_names'},
        ],
      };
      final body = registryPrBody(
        manifest: m,
        upstream: {...m, 'version': '1.0.0'},
        artifactSizes: const {},
        firstParty: true,
      );
      expect(body, contains('No permission changes.'));
      expect(
        body,
        contains(
          'First-party publish (`--first-party`): `trust_tier` '
          '(`official`) and provenance (`core`)',
        ),
      );
      expect(body, contains('`content_hash_sha256`: `$h1`\n'));
    });

    test('the body the submit flow sends matches registryPrBody', () async {
      final w = FakeWorld()..served[_servedWasmUrl] = _wasmA;
      final r = await _publish(w, _pluginDir(_manifest()), submit: true);
      expect(r.code, 0, reason: '$r');
      final create = w.calls.singleWhere(
        (c) => c.line.startsWith('gh pr create'),
      );
      final body = create.args[create.args.indexOf('--body') + 1];
      expect(
        body,
        registryPrBody(
          manifest: _written(w),
          upstream: null,
          artifactSizes: {'wasm_url': _wasmA.length},
        ),
      );
    });
  });

  group('permissionDelta', () {
    test('accepts bare-string permission ids', () {
      final d = permissionDelta(
        {
          'permissions': ['read:a', 'read:b'],
        },
        {
          'permissions': [
            'read:a',
            {'id': 'read:c'},
          ],
        },
      );
      expect(d.added.map((p) => p.id), ['read:c']);
      expect(d.removed, ['read:b']);
      expect(d.scopeChanged, isEmpty);
    });

    test('scope key order is not a change', () {
      final d = permissionDelta(
        {
          'permissions': [
            {
              'id': 'network:fetch',
              'scope': {'a': 1, 'b': 2},
            },
          ],
        },
        {
          'permissions': [
            {
              'id': 'network:fetch',
              'scope': {'b': 2, 'a': 1},
            },
          ],
        },
      );
      expect(d.isEmpty, isTrue);
    });
  });
}
