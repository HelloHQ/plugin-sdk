// Interop of the CLI's author signing with the registry's own tooling and the
// app's ML-DSA-65 verifier.
//
//  - A sidecar the CLI writes must pass `scripts/author-signatures.mjs check`
//    of the registry, on a synthetic registry that enrolls the generated keys.
//  - A sidecar `author-signatures.mjs sign` writes must verify in Dart, and
//    format identically, so the script and the CLI are interchangeable.
//  - The CLI's ML-DSA-65 signature must verify with the app's verifier
//    (mldsa-verify's `mldsa65_verify`), loaded from $HQPLUGIN_MLDSA_VERIFY_LIB.
//
// Needs node, the registry checkout (HELLOHQ_PLUGIN_REGISTRY_DIR) and OpenSSL
// 3.5+; each group skips without them (see test/support/author_test_support).
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';
import 'package:hqplugin/src/author_signing.dart';
import 'package:hqplugin/src/keys_cmd.dart';
import 'package:hqplugin/src/publish.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

import 'support/author_test_support.dart';

final _caps = detectOpenSsl();

const _id = 'com.example.interop';

Map<String, dynamic> _verifiedManifest([
  Map<String, dynamic> extra = const {},
]) => {
  'id': _id,
  'name': 'Interop',
  'version': '1.2.3',
  'provenance': 'community',
  'trust_tier': 'verified',
  'execution_mode': 'wasm',
  'content_hash_sha256': 'ab' * 32,
  'wasm_url': 'https://example.com/plugin.wasm',
  ...extra,
};

String? _skipRegistryScript() {
  final pq = skipUnlessPq(_caps);
  if (pq != null) return pq;
  return skipUnlessAuthorSignaturesScript();
}

/// Generated keys, their signer and the enrolled public keys.
class _Author {
  _Author(this.dir, this.tool, this.signer, this.publicKeys);
  final Directory dir;
  final OpenSslTool tool;
  final OpenSslHybridSigner signer;
  final Map<String, String> publicKeys;
  String get edKey => p.join(dir.path, kEd25519KeyFile);
  String get mlKey => p.join(dir.path, kMlDsa65KeyFile);
}

Future<_Author> _newAuthor() async {
  final dir = tempDir('hqplugin_interop_keys_');
  final err = StringBuffer();
  final code = await runKeysGenerate(
    outDir: dir.path,
    opensslPath: _caps.executable,
    environment: const {},
    out_: StringBuffer(),
    err_: err,
  );
  expect(code, 0, reason: '$err');
  final tool = OpenSslTool(
    run: (e, a) => Process.run(e, a),
    executable: _caps.executable,
  );
  final signer = await OpenSslHybridSigner.open(tool, dir.path);
  final pub = Map<String, String>.from(
    jsonDecode(File(p.join(dir.path, kPublicKeysFile)).readAsStringSync())
        as Map,
  );
  return _Author(dir, tool, signer, pub);
}

Future<String> _cliSidecar(_Author a, Map<String, dynamic> manifest) async {
  final sigs = await a.signer.sign(artifactStatement(manifest));
  return sigs.toSidecarJson();
}

Future<ProcessResult> _check(Directory registry) => runNode([
  p.join(registryRoot().path, 'scripts', 'author-signatures.mjs'),
  'check',
  registry.path,
]);

void main() {
  group('the registry checks a CLI-signed sidecar', () {
    test('a Verified manifest with the CLI signatures is sound', () async {
      final a = await _newAuthor();
      final manifest = _verifiedManifest();
      final sidecar = await _cliSidecar(a, manifest);
      final registry = await syntheticRegistry(
        tool: a.tool,
        publicKeys: a.publicKeys,
        ed25519KeyPath: a.edKey,
        manifest: manifest,
        sidecar: sidecar,
      );
      final r = await _check(registry);
      expect('${r.stdout}${r.stderr}', contains('author signatures are sound'));
      expect(r.exitCode, 0);
    }, skip: _skipRegistryScript());

    test(
      'the statement must be for trust_tier verified, not community',
      () async {
        final a = await _newAuthor();
        final sidecar = await _cliSidecar(
          a,
          _verifiedManifest({'trust_tier': null}),
        );
        final registry = await syntheticRegistry(
          tool: a.tool,
          publicKeys: a.publicKeys,
          ed25519KeyPath: a.edKey,
          manifest: _verifiedManifest(),
          sidecar: sidecar,
        );
        final r = await _check(registry);
        expect(r.exitCode, 1);
        expect('${r.stdout}', contains('matched 0'));
      },
      skip: _skipRegistryScript(),
    );

    test(
      'the PR manifest (tier unset) is refused until the tier is set',
      () async {
        final a = await _newAuthor();
        final prManifest = _verifiedManifest({'trust_tier': null});
        final sidecar = await _cliSidecar(a, verifiedTierView(prManifest));
        final pr = await syntheticRegistry(
          tool: a.tool,
          publicKeys: a.publicKeys,
          ed25519KeyPath: a.edKey,
          manifest: prManifest,
          sidecar: sidecar,
        );
        final before = await _check(pr);
        expect(before.exitCode, 1);
        expect('${before.stdout}', contains('only a Verified plugin'));
        // The registry team sets the tier at merge: now the signatures check.
        File(
          p.join(pr.path, 'plugins', _id, 'manifest.json'),
        ).writeAsStringSync(jsonEncode(verifiedTierView(prManifest)));
        final after = await _check(pr);
        expect(after.exitCode, 0, reason: '${after.stdout}${after.stderr}');
      },
      skip: _skipRegistryScript(),
    );

    test(
      'a changed hash, another author or another prefix is refused',
      () async {
        final a = await _newAuthor();
        final b = await _newAuthor();
        final manifest = _verifiedManifest();
        final sidecar = await _cliSidecar(a, manifest);

        final tampered = await syntheticRegistry(
          tool: a.tool,
          publicKeys: a.publicKeys,
          ed25519KeyPath: a.edKey,
          manifest: _verifiedManifest({'content_hash_sha256': 'cd' * 32}),
          sidecar: sidecar,
        );
        expect((await _check(tampered)).exitCode, 1);

        final otherKey = await syntheticRegistry(
          tool: b.tool,
          publicKeys: b.publicKeys,
          ed25519KeyPath: b.edKey,
          manifest: manifest,
          sidecar: sidecar,
        );
        final r = await _check(otherKey);
        expect(r.exitCode, 1);
        expect('${r.stdout}', contains('matched 0'));

        final otherPrefix = await syntheticRegistry(
          tool: a.tool,
          publicKeys: a.publicKeys,
          ed25519KeyPath: a.edKey,
          manifest: manifest,
          sidecar: sidecar,
          idPrefix: 'org.elsewhere.',
        );
        expect((await _check(otherPrefix)).exitCode, 1);
      },
      skip: _skipRegistryScript(),
    );
  });

  group('hqplugin publish --sign-key against the real registry scripts', () {
    final wasm = [0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, 0x0a];
    const wasmUrl =
        'https://github.com/octocat/interop/releases/download/v1.2.3/plugin.wasm';

    /// gh, git and the build/verify scripts are faked; openssl and the
    /// registry's author-signatures.mjs are the real thing.
    Future<
      ({int code, String out, String err, List<String> calls, String? sidecar})
    >
    publish(_Author signer, _Author enrolled) async {
      final reg = registryRoot();
      final synth = await syntheticRegistry(
        tool: enrolled.tool,
        publicKeys: enrolled.publicKeys,
        ed25519KeyPath: enrolled.edKey,
        manifest: _verifiedManifest({'trust_tier': null}),
      );
      File(p.join(synth.path, 'plugins', _id, 'manifest.json')).deleteSync();
      final plugin = tempDir('hqplugin_interop_plugin_');
      File(p.join(plugin.path, 'manifest.json')).writeAsStringSync(
        jsonEncode({
          'id': _id,
          'name': 'Interop',
          'version': '1.2.3',
          'min_host_version': '1.0.0',
          'execution_mode': 'wasm',
          'wasm_url': wasmUrl,
          'content_hash_sha256': '0' * 64,
          'ui_type': 'declarative',
          'licensing': {'kind': 'open_source', 'spdx': 'MIT'},
          'permissions': <Object>[],
        }),
      );
      final calls = <String>[];
      String? sidecar;
      Future<ProcessResult> run(
        String exe,
        List<String> args, {
        String? workingDirectory,
      }) async {
        calls.add('$exe ${args.join(' ')}');
        if (exe == 'openssl' || exe == _caps.executable) {
          return Process.run(exe, args);
        }
        if (exe == 'node' && args.first == 'scripts/author-signatures.mjs') {
          return Process.run(
            'node',
            args,
            workingDirectory: workingDirectory,
            environment: envWithOpensslFirst(),
          );
        }
        switch ((exe, args)) {
          case ('gh', ['api', 'repos/HelloHQ/plugin-registry', ...]):
            return ProcessResult(1, 0, 'true\n', '');
          case ('gh', ['api', 'user', ...]):
            return ProcessResult(1, 0, 'octocat\n', '');
          case ('gh', ['repo', 'clone', _, final dir]):
            Directory(dir).createSync(recursive: true);
            for (final sub in ['scripts', 'authors']) {
              final src = Directory(
                p.join(sub == 'scripts' ? reg.path : synth.path, sub),
              );
              Directory(p.join(dir, sub)).createSync();
              for (final f in src.listSync().whereType<File>()) {
                f.copySync(p.join(dir, sub, p.basename(f.path)));
              }
            }
            return ProcessResult(1, 0, '', '');
          case ('gh', ['pr', 'create', ...]):
            return ProcessResult(
              1,
              0,
              'https://github.com/HelloHQ/plugin-registry/pull/1\n',
              '',
            );
          case ('git', ['show', ...]):
            return ProcessResult(1, 128, '', 'fatal: path does not exist');
          case ('git', ['add', ...]):
            final f = File(
              p.join(
                workingDirectory!,
                'plugins',
                _id,
                'author-signatures.json',
              ),
            );
            sidecar = f.existsSync() ? f.readAsStringSync() : null;
            return ProcessResult(1, 0, '', '');
        }
        return ProcessResult(1, 0, '', '');
      }

      Future<HttpHop> get(Uri url, {required int maxBytes}) async {
        if (url.toString() == wasmUrl) return HttpHop(200, body: wasm);
        if (url.path.endsWith('/scripts/author-signatures.mjs')) {
          return const HttpHop(200, body: [0x2f]);
        }
        return const HttpHop(404);
      }

      final out = StringBuffer();
      final err = StringBuffer();
      final code = await runPublish(
        submit: true,
        signKeyDir: signer.dir.path,
        opensslPath: _caps.executable,
        environment: const {},
        out_: out,
        err_: err,
        commandRunner: run,
        httpGet: get,
        workingDirectory: plugin.path,
      );
      return (
        code: code,
        out: '$out',
        err: '$err',
        calls: calls,
        sidecar: sidecar,
      );
    }

    test(
      'an enrolled author publishes and the registry check passes',
      () async {
        final a = await _newAuthor();
        final r = await publish(a, a);
        expect(r.code, 0, reason: '${r.out}\n${r.err}');
        expect(r.out, contains('author signatures are sound'));
        final sigs = AuthorSignatures.parseSidecar(r.sidecar!);
        expect(sigs.mlDsa65, hasLength(3309));
        expect(r.calls.any((c) => c.startsWith('git push')), isTrue);
        expect('${r.out}${r.err}', isNot(contains('PRIVATE KEY')));
      },
      skip: _skipRegistryScript(),
    );

    test('a key the registry has not enrolled is refused (exit 65)', () async {
      final signer = await _newAuthor();
      final enrolled = await _newAuthor();
      final r = await publish(signer, enrolled);
      expect(r.code, 65, reason: '${r.out}\n${r.err}');
      expect(r.err, contains('(matched 0)'));
      expect(r.err, contains('authors/README.md'));
      expect(r.calls.any((c) => c.startsWith('git push')), isFalse);
    }, skip: _skipRegistryScript());
  });

  group('author-signatures.mjs sign and the CLI are interchangeable', () {
    test(
      'a script-made sidecar verifies in Dart and has the same format',
      () async {
        final a = await _newAuthor();
        final manifest = _verifiedManifest();
        final work = tempDir('hqplugin_interop_sign_');
        final manifestFile = File(p.join(work.path, 'manifest.json'))
          ..writeAsStringSync(jsonEncode(manifest));
        final r = await runNode([
          p.join(registryRoot().path, 'scripts', 'author-signatures.mjs'),
          'sign',
          manifestFile.path,
          '--ed25519-key',
          a.edKey,
          '--mldsa-key',
          a.mlKey,
        ]);
        expect(r.exitCode, 0, reason: '${r.stdout}${r.stderr}');
        final text = File(
          p.join(work.path, 'author-signatures.json'),
        ).readAsStringSync();
        final parsed = AuthorSignatures.parseSidecar(text);
        expect(
          await a.signer.verify(artifactStatement(manifest), parsed),
          isTrue,
        );
        expect(
          parsed.toSidecarJson(),
          text,
          reason: 'same bytes as the script writes',
        );
        // Ed25519 is deterministic, so the CLI reproduces the script's signature.
        final mine = await a.signer.sign(artifactStatement(manifest));
        expect(mine.ed25519, parsed.ed25519);
        expect(mine.mlDsa65, isNot(parsed.mlDsa65), reason: 'ML-DSA is hedged');
        expect(
          await a.signer.verify(artifactStatement(manifest), mine),
          isTrue,
        );
      },
      skip: _skipRegistryScript(),
    );

    test('the statement bytes match artifact-statement.mjs', () async {
      final work = tempDir('hqplugin_interop_stmt_');
      for (final m in [
        _verifiedManifest(),
        _verifiedManifest({'trust_tier': null, 'execution_mode': null}),
        _verifiedManifest({'ui_bundle_hash_sha256': 'ef' * 32}),
      ]) {
        final file = File(p.join(work.path, 'manifest.json'))
          ..writeAsStringSync(jsonEncode(m));
        final r = await runNode([
          p.join(registryRoot().path, 'scripts', 'artifact-statement.mjs'),
          file.path,
        ], binaryStdout: true);
        expect(r.exitCode, 0);
        expect(r.stdout as List<int>, artifactStatement(m));
      }
    }, skip: skipUnlessAuthorSignaturesScript());
  });

  group('the app verifier accepts the CLI ML-DSA-65 signature', () {
    final libPath = Platform.environment['HQPLUGIN_MLDSA_VERIFY_LIB'];

    test(
      'mldsa65_verify is 1 for the signature, 0 for tampering',
      () async {
        final a = await _newAuthor();
        final statement = artifactStatement(
          verifiedTierView(_verifiedManifest()),
        );
        final sigs = await a.signer.sign(statement);
        final pk = a.signer.publicKeys[HybridAlgorithm.mlDsa65]!;

        final verify = DynamicLibrary.open(libPath!)
            .lookupFunction<
              Int32 Function(
                Pointer<Uint8>,
                Size,
                Pointer<Uint8>,
                Size,
                Pointer<Uint8>,
                Size,
              ),
              int Function(
                Pointer<Uint8>,
                int,
                Pointer<Uint8>,
                int,
                Pointer<Uint8>,
                int,
              )
            >('mldsa65_verify');

        bool ok(Uint8List pk, List<int> msg, Uint8List sig) {
          final pp = malloc<Uint8>(pk.length);
          final mp = malloc<Uint8>(msg.length);
          final sp = malloc<Uint8>(sig.length);
          try {
            pp.asTypedList(pk.length).setAll(0, pk);
            mp.asTypedList(msg.length).setAll(0, msg);
            sp.asTypedList(sig.length).setAll(0, sig);
            return verify(pp, pk.length, mp, msg.length, sp, sig.length) == 1;
          } finally {
            malloc.free(pp);
            malloc.free(mp);
            malloc.free(sp);
          }
        }

        expect(ok(pk, statement, sigs.mlDsa65), isTrue);
        expect(
          ok(
            pk,
            artifactStatement(_verifiedManifest({'version': '1.2.4'})),
            sigs.mlDsa65,
          ),
          isFalse,
        );
        expect(
          ok(pk, statement, Uint8List.fromList(sigs.mlDsa65)..[7] ^= 1),
          isFalse,
        );
        // The same message under a community tier is a different statement.
        expect(
          ok(
            pk,
            artifactStatement(_verifiedManifest({'trust_tier': null})),
            sigs.mlDsa65,
          ),
          isFalse,
        );
      },
      skip:
          skipUnlessPq(_caps) ??
          (libPath == null || libPath.isEmpty
              ? 'set HQPLUGIN_MLDSA_VERIFY_LIB to a built mldsa-verify library'
              : null),
    );
  });
}
