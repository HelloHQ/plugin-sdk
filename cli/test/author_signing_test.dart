// Author-held signing: the artifact statement, the OpenSSL-backed signer
// (against a fake openssl, hermetic) and the same signer against a real
// openssl. ML-DSA-65 needs OpenSSL 3.5+: those tests skip without it unless
// HQPLUGIN_REQUIRE_OPENSSL_PQ=1, which makes them fail instead.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:hqplugin/src/author_signing.dart';
import 'package:hqplugin/src/keys_cmd.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

import 'support/author_test_support.dart';

final _caps = detectOpenSsl();

Map<String, dynamic> _manifest([Map<String, dynamic> overrides = const {}]) {
  final m = <String, dynamic>{
    'id': 'com.example.demo',
    'version': '2.1.0',
    'content_hash_sha256': 'ab' * 32,
  };
  overrides.forEach((k, v) => v == null ? m.remove(k) : m[k] = v);
  return m;
}

String _text(Map<String, dynamic> m) => utf8.decode(artifactStatement(m));

Matcher _throwsStatement(Pattern message) => throwsA(
  isA<StatementException>().having(
    (e) => e.message,
    'message',
    contains(message),
  ),
);

Future<SignerException> _signerError(Future<Object?> Function() body) async {
  try {
    await body();
  } on SignerException catch (e) {
    return e;
  }
  fail('expected a SignerException');
}

void main() {
  group('artifact statement', () {
    test('matches the registry vectors', () {
      final reg = registryCheckout();
      if (reg == null) {
        if (requireRegistry) registryRoot();
        markTestSkipped('plugin-registry checkout not found');
        return;
      }
      final file = File(
        p.join(
          reg.path,
          'tests',
          'fixtures',
          'artifact-statement-vectors.json',
        ),
      );
      final vectors =
          (jsonDecode(file.readAsStringSync()) as Map)['vectors'] as List;
      expect(vectors.length, greaterThanOrEqualTo(3));
      for (final v in vectors) {
        final m = Map<String, dynamic>.from(v['manifest'] as Map);
        expect(_text(m), v['statement'], reason: v['name'] as String);
      }
    }, skip: skipUnlessRegistry());

    test('is seven LF-terminated lines starting with the version', () {
      final text = _text(_manifest());
      expect(text, startsWith('hellohq-plugin-artifact/v1\n'));
      expect(text, endsWith('\n'));
      expect(text, isNot(contains('\r')));
      expect(text.split('\n'), hasLength(8));
    });

    test('absent tier, mode and UI hash take their defaults', () {
      expect(
        _text(_manifest()),
        'hellohq-plugin-artifact/v1\n'
        'id=com.example.demo\n'
        'version=2.1.0\n'
        'trust_tier=community\n'
        'execution_mode=wasm\n'
        'content_hash_sha256=${'ab' * 32}\n'
        'ui_bundle_hash_sha256=\n',
      );
    });

    test('carries the given tier, mode and UI bundle hash', () {
      expect(
        _text(
          _manifest({
            'trust_tier': 'verified',
            'execution_mode': 'sidecar',
            'ui_bundle_hash_sha256': 'cd' * 32,
          }),
        ),
        'hellohq-plugin-artifact/v1\n'
        'id=com.example.demo\n'
        'version=2.1.0\n'
        'trust_tier=verified\n'
        'execution_mode=sidecar\n'
        'content_hash_sha256=${'ab' * 32}\n'
        'ui_bundle_hash_sha256=${'cd' * 32}\n',
      );
    });

    test('a null UI hash is the same as an absent one', () {
      expect(
        _text(_manifest({'ui_bundle_hash_sha256': null})),
        _text(_manifest()),
      );
    });

    test('a line break in any field is refused', () {
      for (final key in ['id', 'version', 'trust_tier', 'execution_mode']) {
        for (final bad in ['x\ny', 'x\r', '\nx']) {
          expect(
            () => artifactStatement(_manifest({key: bad})),
            _throwsStatement('$key contains a line break'),
            reason: '$key=${jsonEncode(bad)}',
          );
        }
      }
    });

    test('a hash that is not 64 lowercase hex is refused', () {
      for (final bad in [
        'AB' * 32,
        'ab' * 31,
        'zz' * 32,
        '',
        7,
        null,
        '${'ab' * 32}\n',
      ]) {
        expect(
          () => artifactStatement(_manifest({'content_hash_sha256': bad})),
          _throwsStatement('content_hash_sha256 must be 64 lowercase hex'),
          reason: '$bad',
        );
      }
      for (final bad in ['AB' * 32, '']) {
        expect(
          () => artifactStatement(_manifest({'ui_bundle_hash_sha256': bad})),
          _throwsStatement('ui_bundle_hash_sha256 must be 64 lowercase hex'),
          reason: '"$bad"',
        );
      }
    });

    test('a non-string field is refused', () {
      expect(
        () => artifactStatement(_manifest({'version': 1})),
        _throwsStatement('version must be a string'),
      );
      expect(
        () => artifactStatement(_manifest({'id': null})),
        _throwsStatement('id must be a string'),
      );
    });

    test('verifiedTierView forces verified and leaves the rest alone', () {
      final m = _manifest({'trust_tier': 'community', 'name': 'Demo'});
      final v = verifiedTierView(m);
      expect(v['trust_tier'], 'verified');
      expect(v['name'], 'Demo');
      expect(m['trust_tier'], 'community', reason: 'the input is not mutated');
      expect(
        _text(verifiedTierView(_manifest())),
        contains('\ntrust_tier=verified\n'),
      );
    });
  });

  group('author-signatures.json', () {
    AuthorSignatures sigs() => AuthorSignatures(
      ed25519: Uint8List(64)..fillRange(0, 64, 7),
      mlDsa65: Uint8List(3309)..fillRange(0, 3309, 9),
    );

    test('is 2-space JSON with a trailing newline, ed25519 first', () {
      final text = sigs().toSidecarJson();
      expect(text, endsWith('}\n'));
      expect(
        text,
        startsWith('{\n  "signatures": [\n    {\n      "sig_alg": "ed25519",'),
      );
      final body = jsonDecode(text) as Map;
      expect(
        [for (final s in body['signatures']) s['sig_alg']],
        ['ed25519', 'ml-dsa-65'],
      );
    });

    test('parses back to the same bytes', () {
      final back = AuthorSignatures.parseSidecar(sigs().toSidecarJson());
      expect(back.ed25519, sigs().ed25519);
      expect(back.mlDsa65, sigs().mlDsa65);
    });

    test('parseSidecar refuses what the registry refuses', () {
      String file(List<Map<String, String>> s) => jsonEncode({'signatures': s});
      final ed = base64.encode(Uint8List(64));
      final ml = base64.encode(Uint8List(3309));
      void bad(String text, Pattern why) => expect(
        () => AuthorSignatures.parseSidecar(text),
        throwsA(
          isA<FormatException>().having((e) => e.message, 'm', contains(why)),
        ),
      );
      bad('nope', 'not valid JSON');
      bad('{}', 'needs a signatures array');
      bad(
        file([
          {'sig_alg': 'ed25519', 'signature': ed},
        ]),
        'exactly one ed25519',
      );
      bad(
        file([
          {'sig_alg': 'ed25519', 'signature': ed},
          {'sig_alg': 'ed25519', 'signature': ed},
        ]),
        'exactly one ed25519',
      );
      bad(
        file([
          {'sig_alg': 'ed25519', 'signature': base64.encode(Uint8List(63))},
          {'sig_alg': 'ml-dsa-65', 'signature': ml},
        ]),
        'ed25519 signature must be 64 bytes',
      );
      bad(
        file([
          {'sig_alg': 'ed25519', 'signature': ed},
          {'sig_alg': 'ml-dsa-65', 'signature': base64.encode(Uint8List(3308))},
        ]),
        'ml-dsa-65 signature must be 3309 bytes',
      );
      bad(
        file([
          {'sig_alg': 'ed25519', 'signature': ed},
          {'sig_alg': 'ml-dsa-65', 'signature': '$ml!'},
        ]),
        'must be base64',
      );
    });
  });

  group('resolveOpenSslExecutable', () {
    test('flag beats env beats PATH', () {
      expect(
        resolveOpenSslExecutable('/a/openssl', {'HQPLUGIN_OPENSSL': '/b'}),
        '/a/openssl',
      );
      expect(
        resolveOpenSslExecutable(null, {'HQPLUGIN_OPENSSL': '/b/openssl'}),
        '/b/openssl',
      );
      expect(resolveOpenSslExecutable('', {'HQPLUGIN_OPENSSL': ''}), 'openssl');
      expect(resolveOpenSslExecutable(null, {}), 'openssl');
    });
  });

  group('sanitizeOpenSslText', () {
    test('drops PEM armour, base64 runs and hex dumps', () {
      final text = sanitizeOpenSslText(
        'Could not read key\n'
        '-----BEGIN PRIVATE KEY-----\n'
        '${'A' * 64}\n'
        '-----END PRIVATE KEY-----\n'
        '    00:11:22:33:44:55:66:77:88:99\n'
        'error:1234:bad decrypt\n',
      );
      expect(text, 'Could not read key\nerror:1234:bad decrypt');
    });

    test('caps the length', () {
      expect(sanitizeOpenSslText('x ' * 1000).length, lessThan(700));
    });
  });

  group('OpenSslTool with a fake openssl', () {
    late FakeOpenSsl fake;
    late OpenSslTool tool;
    setUp(() {
      fake = FakeOpenSsl();
      tool = OpenSslTool(
        run: fake.call,
        executable: '/opt/openssl/bin/openssl',
      );
    });

    test('preflight reports the version it found', () async {
      expect(await tool.preflight(), startsWith('OpenSSL 3.6.0'));
      expect(fake.calls.first, ['version']);
      expect(fake.calls[1].sublist(0, 3), [
        'genpkey',
        '-algorithm',
        'ML-DSA-65',
      ]);
      expect(fake.calls[1][3], '-out');
    });

    test('a missing openssl is exit 69 and says how to get one', () async {
      fake.installed = false;
      final e = await _signerError(tool.preflight);
      expect(e.code, 69);
      expect(e.message, contains('was not found'));
      expect(e.message, contains('/opt/openssl/bin/openssl'));
      expect(e.message, contains('3.5'));
      expect(e.message, contains('--openssl'));
      expect(e.message, contains('HQPLUGIN_OPENSSL'));
      expect(e.message, contains('brew'));
    });

    test('an openssl without ML-DSA is exit 69 naming its version', () async {
      fake
        ..pq = false
        ..versionLine = 'OpenSSL 3.4.1 11 Feb 2025';
      final e = await _signerError(tool.preflight);
      expect(e.code, 69);
      expect(e.message, contains('OpenSSL 3.4.1 11 Feb 2025'));
      expect(e.message, contains('cannot create ML-DSA-65 keys'));
      expect(e.message, contains('3.5 or newer'));
      expect(e.message, contains('Git for'));
    });

    test('the probe key never outlives the call', () async {
      await tool.preflight();
      final probe = fake.calls[1][4];
      expect(File(probe).existsSync(), isFalse);
      expect(Directory(p.dirname(probe)).existsSync(), isFalse);
    });

    test('signing uses pkeyutl -sign -rawin with the key as a path', () async {
      final keys = FakeOpenSsl.keyDir();
      final key = p.join(keys.path, kEd25519KeyFile);
      final sig = await tool.sign(
        HybridAlgorithm.ed25519,
        key,
        utf8.encode('hi'),
      );
      expect(sig, hasLength(64));
      final args = fake.calls.single;
      expect(args.sublist(0, 4), ['pkeyutl', '-sign', '-inkey', key]);
      expect(args[4], '-rawin');
      expect(args[5], '-in');
      expect(args[7], '-out');
      expect(p.basename(args[6]), 'msg');
      expect(p.basename(args[8]), 'sig');
    });

    test('verifying derives the public key and uses -pubin', () async {
      final keys = FakeOpenSsl.keyDir();
      final key = p.join(keys.path, kMlDsa65KeyFile);
      final msg = utf8.encode('statement');
      final sig = await tool.sign(HybridAlgorithm.mlDsa65, key, msg);
      expect(sig, hasLength(3309));
      fake.calls.clear();
      expect(await tool.verify(HybridAlgorithm.mlDsa65, key, msg, sig), isTrue);
      expect(fake.calls[0].sublist(0, 4), ['pkey', '-in', key, '-pubout']);
      expect(fake.calls[1].sublist(0, 4), [
        'pkeyutl',
        '-verify',
        '-pubin',
        '-inkey',
      ]);
      expect(fake.calls[1], contains('-rawin'));
      expect(fake.calls[1], contains('-sigfile'));
      expect(
        await tool.verify(
          HybridAlgorithm.mlDsa65,
          key,
          utf8.encode('other'),
          sig,
        ),
        isFalse,
      );
    });

    test(
      'temp files are deleted after signing, verifying and on failure',
      () async {
        final keys = FakeOpenSsl.keyDir();
        final key = p.join(keys.path, kEd25519KeyFile);
        final sig = await tool.sign(HybridAlgorithm.ed25519, key, [1, 2, 3]);
        await tool.verify(HybridAlgorithm.ed25519, key, [1, 2, 3], sig);
        await tool.rawPublicKey(HybridAlgorithm.ed25519, key);
        fake.signExit = 1;
        await _signerError(() => tool.sign(HybridAlgorithm.ed25519, key, [1]));
        expect(fake.tempDirs, isNotEmpty);
        for (final d in fake.tempDirs) {
          expect(Directory(d).existsSync(), isFalse, reason: d);
        }
      },
    );

    test('rawPublicKey returns the key after the SPKI header', () async {
      final keys = FakeOpenSsl.keyDir();
      final ed = await tool.rawPublicKey(
        HybridAlgorithm.ed25519,
        p.join(keys.path, kEd25519KeyFile),
      );
      final ml = await tool.rawPublicKey(
        HybridAlgorithm.mlDsa65,
        p.join(keys.path, kMlDsa65KeyFile),
      );
      expect(ed, hasLength(32));
      expect(ml, hasLength(1952));
    });

    test('a key of the wrong algorithm is exit 65', () async {
      final keys = FakeOpenSsl.keyDir();
      final e = await _signerError(
        () => tool.rawPublicKey(
          HybridAlgorithm.ed25519,
          p.join(keys.path, kMlDsa65KeyFile),
        ),
      );
      expect(e.code, 65);
      expect(e.message, contains('is not an ed25519 private key'));
    });

    test('openssl failing to sign is exit 65 with sanitized text', () async {
      final keys = FakeOpenSsl.keyDir();
      fake
        ..signExit = 1
        ..signStderr =
            'Error loading key\n-----BEGIN PRIVATE KEY-----\n${'Q' * 80}\n'
            '-----END PRIVATE KEY-----\n';
      final e = await _signerError(
        () => tool.sign(
          HybridAlgorithm.ed25519,
          p.join(keys.path, kEd25519KeyFile),
          [1],
        ),
      );
      expect(e.code, 65);
      expect(e.message, contains('Error loading key'));
      expect(e.message, isNot(contains('BEGIN')));
      expect(e.message, isNot(contains('QQQQ')));
    });

    test('a signature of the wrong size is exit 70', () async {
      final keys = FakeOpenSsl.keyDir();
      fake.signatureBytes = 63;
      final e = await _signerError(
        () => tool.sign(
          HybridAlgorithm.ed25519,
          p.join(keys.path, kEd25519KeyFile),
          [1],
        ),
      );
      expect(e.code, 70);
      expect(e.message, contains('63-byte'));
    });
  });

  group('OpenSslHybridSigner with a fake openssl', () {
    late FakeOpenSsl fake;
    late OpenSslTool tool;
    setUp(() {
      fake = FakeOpenSsl();
      tool = OpenSslTool(run: fake.call);
    });

    test('opens a key directory, signs and verifies both halves', () async {
      final keys = FakeOpenSsl.keyDir();
      final signer = await OpenSslHybridSigner.open(tool, keys.path);
      expect(signer.publicKeys[HybridAlgorithm.ed25519], hasLength(32));
      expect(signer.publicKeys[HybridAlgorithm.mlDsa65], hasLength(1952));
      final statement = artifactStatement(
        _manifest({'trust_tier': 'verified'}),
      );
      final sigs = await signer.sign(statement);
      expect(sigs.ed25519, hasLength(64));
      expect(sigs.mlDsa65, hasLength(3309));
      expect(await signer.verify(statement, sigs), isTrue);
      final other = artifactStatement(_manifest({'version': '2.1.1'}));
      expect(await signer.verify(other, sigs), isFalse);
      final swapped = AuthorSignatures(
        ed25519: sigs.ed25519,
        mlDsa65: Uint8List.fromList(sigs.mlDsa65)..[0] ^= 1,
      );
      expect(await signer.verify(statement, swapped), isFalse);
    });

    test('a missing key directory is exit 66', () async {
      final e = await _signerError(
        () => OpenSslHybridSigner.open(tool, '/no/such/keys'),
      );
      expect(e.code, 66);
      expect(e.message, contains('keys generate'));
      expect(
        fake.calls,
        isEmpty,
        reason: 'nothing runs before the files exist',
      );
    });

    test('a missing key file is exit 66 and names it', () async {
      final keys = FakeOpenSsl.keyDir();
      File(p.join(keys.path, kMlDsa65KeyFile)).deleteSync();
      final e = await _signerError(
        () => OpenSslHybridSigner.open(tool, keys.path),
      );
      expect(e.code, 66);
      expect(e.message, contains(kMlDsa65KeyFile));
    });

    test('an ML-DSA key in the ed25519 slot is exit 65', () async {
      final keys = FakeOpenSsl.keyDir();
      File(
        p.join(keys.path, kMlDsa65KeyFile),
      ).copySync(p.join(keys.path, kEd25519KeyFile));
      final e = await _signerError(
        () => OpenSslHybridSigner.open(tool, keys.path),
      );
      expect(e.code, 65);
      expect(e.message, contains('is not an ed25519 private key'));
    });

    test('an old openssl is exit 69 before any key is read', () async {
      final keys = FakeOpenSsl.keyDir();
      fake.pq = false;
      final e = await _signerError(
        () => OpenSslHybridSigner.open(tool, keys.path),
      );
      expect(e.code, 69);
      expect(
        fake.calls.where((c) => c.first == 'pkey'),
        isEmpty,
        reason: 'the keys are not touched',
      );
    });

    test('keys that sign a probe that does not verify are exit 70', () async {
      final keys = FakeOpenSsl.keyDir();
      fake.verifies = false;
      final e = await _signerError(
        () => OpenSslHybridSigner.open(tool, keys.path),
      );
      expect(e.code, 70);
    });

    test(
      'no private key bytes appear in anything the signer reports',
      () async {
        final keys = FakeOpenSsl.keyDir();
        fake.signExit = 1;
        final e = await _signerError(
          () => OpenSslHybridSigner.open(tool, keys.path),
        );
        expect(e.message, isNot(contains(kFakeSecretMarker)));
        expect(e.message, isNot(contains('BEGIN')));
        fake.signExit = 0;
        final signer = await OpenSslHybridSigner.open(tool, keys.path);
        final sigs = await signer.sign([1, 2, 3]);
        expect(sigs.toSidecarJson(), isNot(contains(kFakeSecretMarker)));
        // The argument lists hold paths, never key contents.
        for (final call in fake.calls) {
          for (final a in call) {
            expect(a, isNot(contains(kFakeSecretMarker)));
          }
        }
      },
    );
  });

  group('keys generate with a fake openssl', () {
    late FakeOpenSsl fake;
    late StringBuffer out;
    late StringBuffer err;
    setUp(() {
      fake = FakeOpenSsl();
      out = StringBuffer();
      err = StringBuffer();
    });

    Future<int> generate(String? dir) => runKeysGenerate(
      outDir: dir,
      runner: fake.call,
      environment: const {},
      out_: out,
      err_: err,
    );

    test('--out is required (exit 64)', () async {
      expect(await generate(null), 64);
      expect(err.toString(), contains('--out'));
    });

    test(
      'writes both keys and public-keys.json, prints only public keys',
      () async {
        final dir = tempDir('hqplugin_keys_');
        final target = p.join(dir.path, 'author');
        expect(await generate(target), 0, reason: '$err');
        expect(File(p.join(target, kEd25519KeyFile)).existsSync(), isTrue);
        expect(File(p.join(target, kMlDsa65KeyFile)).existsSync(), isTrue);
        final pub =
            jsonDecode(File(p.join(target, kPublicKeysFile)).readAsStringSync())
                as Map<String, dynamic>;
        expect(pub.keys, ['ed25519', 'ml-dsa-65']);
        expect(base64.decode(pub['ed25519'] as String), hasLength(32));
        expect(base64.decode(pub['ml-dsa-65'] as String), hasLength(1952));
        expect(
          File(p.join(target, kPublicKeysFile)).readAsStringSync(),
          endsWith('}\n'),
        );
        final shown = '$out';
        expect(shown, contains(pub['ed25519'] as String));
        expect(shown, contains(pub['ml-dsa-65'] as String));
        expect(shown, contains(p.join(target, kEd25519KeyFile)));
        expect(shown, contains('Back both .pem files up offline'));
        expect(shown, contains('revoke'));
        expect(shown, isNot(contains(kFakeSecretMarker)));
        expect(shown, isNot(contains('BEGIN')));
        expect('$err', isNot(contains(kFakeSecretMarker)));
        if (!Platform.isWindows) {
          expect(
            File(p.join(target, kEd25519KeyFile)).statSync().mode & 0x1ff,
            0x180,
          );
          expect(
            File(p.join(target, kMlDsa65KeyFile)).statSync().mode & 0x1ff,
            0x180,
          );
          expect(Directory(target).statSync().mode & 0x1ff, 0x1c0);
        }
      },
    );

    test('never overwrites existing key material (exit 65)', () async {
      final dir = tempDir('hqplugin_keys_');
      await generate(dir.path);
      final before = File(p.join(dir.path, kEd25519KeyFile)).readAsStringSync();
      out.clear();
      expect(await generate(dir.path), 65);
      expect('$err', contains('refusing to overwrite'));
      expect(
        File(p.join(dir.path, kEd25519KeyFile)).readAsStringSync(),
        before,
      );
      // Even one existing file blocks it, and nothing else is written.
      final dir2 = tempDir('hqplugin_keys_');
      File(p.join(dir2.path, kMlDsa65KeyFile)).writeAsStringSync('mine');
      expect(await generate(dir2.path), 65);
      expect(File(p.join(dir2.path, kEd25519KeyFile)).existsSync(), isFalse);
      expect(
        File(p.join(dir2.path, kMlDsa65KeyFile)).readAsStringSync(),
        'mine',
      );
    });

    test('an --out that is a file is exit 65', () async {
      final dir = tempDir('hqplugin_keys_');
      final f = File(p.join(dir.path, 'file'))..writeAsStringSync('x');
      expect(await generate(f.path), 65);
      expect('$err', contains('not a directory'));
    });

    test('a missing or old openssl is exit 69 and creates nothing', () async {
      final dir = tempDir('hqplugin_keys_');
      final target = p.join(dir.path, 'author');
      fake.pq = false;
      expect(await generate(target), 69);
      expect('$err', contains('3.5 or newer'));
      expect(Directory(target).existsSync(), isFalse);
      fake
        ..pq = true
        ..installed = false;
      err.clear();
      expect(await generate(target), 69);
      expect('$err', contains('was not found'));
    });

    test('a failure half way removes what this run made', () async {
      final dir = tempDir('hqplugin_keys_');
      final target = p.join(dir.path, 'author');
      // ED25519 is generated first, then the run fails on ML-DSA.
      var generated = 0;
      Future<ProcessResult> runner(String exe, List<String> args) async {
        if (args.first == 'genpkey' &&
            args[2] == 'ML-DSA-65' &&
            generated++ > 0) {
          return ProcessResult(1, 1, '', 'boom');
        }
        return fake.call(exe, args);
      }

      // The probe consumes the first ML-DSA genpkey, the real one then fails.
      final code = await runKeysGenerate(
        outDir: target,
        runner: runner,
        environment: const {},
        out_: out,
        err_: err,
      );
      expect(code, 69);
      expect(Directory(target).existsSync(), isFalse);
    });

    test('a pre-existing directory is kept when generation fails', () async {
      final dir = tempDir('hqplugin_keys_');
      fake.genEd25519Exit = 3;
      expect(await generate(dir.path), 69);
      expect(Directory(dir.path).existsSync(), isTrue);
      expect(dir.listSync(), isEmpty);
    });
  });

  group('real openssl: Ed25519', () {
    late OpenSslTool tool;
    setUp(
      () => tool = OpenSslTool(
        run: (e, a) => Process.run(e, a),
        executable: _caps.executable,
      ),
    );

    test('generate, public key, sign, verify', () async {
      final dir = tempDir('hqplugin_ed_');
      final key = p.join(dir.path, 'ed.pem');
      await tool.generateKey(HybridAlgorithm.ed25519, key);
      expect(
        await tool.rawPublicKey(HybridAlgorithm.ed25519, key),
        hasLength(32),
      );
      final msg = utf8.encode('hello');
      final sig = await tool.sign(HybridAlgorithm.ed25519, key, msg);
      expect(sig, hasLength(64));
      expect(await tool.verify(HybridAlgorithm.ed25519, key, msg, sig), isTrue);
      expect(
        await tool.verify(
          HybridAlgorithm.ed25519,
          key,
          utf8.encode('hellp'),
          sig,
        ),
        isFalse,
      );
      // Ed25519 is deterministic: signing twice gives the same bytes.
      expect(await tool.sign(HybridAlgorithm.ed25519, key, msg), sig);
    }, skip: skipUnlessEd25519(_caps));

    test('a garbage key file is exit 65', () async {
      final dir = tempDir('hqplugin_ed_');
      final key = File(p.join(dir.path, 'ed.pem'))
        ..writeAsStringSync('not a key');
      final e = await _signerError(
        () => tool.rawPublicKey(HybridAlgorithm.ed25519, key.path),
      );
      expect(e.code, 65);
    }, skip: skipUnlessEd25519(_caps));
  });

  group('real openssl: ML-DSA-65 and the hybrid signer', () {
    late OpenSslTool tool;
    setUp(
      () => tool = OpenSslTool(
        run: (e, a) => Process.run(e, a),
        executable: _caps.executable,
      ),
    );

    test('the configured openssl can do ML-DSA-65', () {
      expect(
        _caps.mlDsa,
        isTrue,
        reason:
            'HQPLUGIN_REQUIRE_OPENSSL_PQ=1 needs OpenSSL 3.5+; '
            '${_caps.executable} is ${_caps.version}',
      );
    }, skip: requirePq ? null : 'only when HQPLUGIN_REQUIRE_OPENSSL_PQ=1');

    test('preflight accepts it', () async {
      expect(await tool.preflight(), startsWith('OpenSSL'));
    }, skip: skipUnlessPq(_caps));

    test('ML-DSA-65 sign / verify', () async {
      final dir = tempDir('hqplugin_ml_');
      final key = p.join(dir.path, 'ml.pem');
      await tool.generateKey(HybridAlgorithm.mlDsa65, key);
      expect(
        await tool.rawPublicKey(HybridAlgorithm.mlDsa65, key),
        hasLength(1952),
      );
      final msg = utf8.encode('statement');
      final sig = await tool.sign(HybridAlgorithm.mlDsa65, key, msg);
      expect(sig, hasLength(3309));
      expect(await tool.verify(HybridAlgorithm.mlDsa65, key, msg, sig), isTrue);
      expect(
        await tool.verify(HybridAlgorithm.mlDsa65, key, utf8.encode('x'), sig),
        isFalse,
      );
      final other = p.join(dir.path, 'other.pem');
      await tool.generateKey(HybridAlgorithm.mlDsa65, other);
      expect(
        await tool.verify(HybridAlgorithm.mlDsa65, other, msg, sig),
        isFalse,
      );
    }, skip: skipUnlessPq(_caps));

    test('keys generate, then the signer round-trips a statement', () async {
      final dir = tempDir('hqplugin_keys_');
      final out = StringBuffer();
      final err = StringBuffer();
      final code = await runKeysGenerate(
        outDir: p.join(dir.path, 'k'),
        opensslPath: _caps.executable,
        environment: const {},
        out_: out,
        err_: err,
      );
      expect(code, 0, reason: '$err');
      final keyDir = p.join(dir.path, 'k');
      expect(
        Directory(keyDir).listSync().map((e) => p.basename(e.path)).toSet(),
        {kEd25519KeyFile, kMlDsa65KeyFile, kPublicKeysFile},
      );
      if (!Platform.isWindows) {
        for (final f in [kEd25519KeyFile, kMlDsa65KeyFile]) {
          expect(
            File(p.join(keyDir, f)).statSync().mode & 0x1ff,
            0x180,
            reason: f,
          );
        }
      }
      // No private key material is printed.
      for (final f in [kEd25519KeyFile, kMlDsa65KeyFile]) {
        final body = File(p.join(keyDir, f)).readAsStringSync();
        final b64 = body
            .split('\n')
            .where((l) => l.isNotEmpty && !l.startsWith('-'));
        for (final line in b64) {
          expect('$out$err', isNot(contains(line)), reason: f);
        }
      }
      expect('$out$err', isNot(contains('PRIVATE KEY')));

      final signer = await OpenSslHybridSigner.open(tool, keyDir);
      final pub =
          jsonDecode(File(p.join(keyDir, kPublicKeysFile)).readAsStringSync())
              as Map<String, dynamic>;
      expect(
        base64.decode(pub['ed25519'] as String),
        signer.publicKeys[HybridAlgorithm.ed25519],
      );
      expect(
        base64.decode(pub['ml-dsa-65'] as String),
        signer.publicKeys[HybridAlgorithm.mlDsa65],
      );

      final statement = artifactStatement(verifiedTierView(_manifest()));
      final sigs = await signer.sign(statement);
      expect(await signer.verify(statement, sigs), isTrue);
      final parsed = AuthorSignatures.parseSidecar(sigs.toSidecarJson());
      expect(await signer.verify(statement, parsed), isTrue);
      expect(
        await signer.verify(artifactStatement(_manifest()), sigs),
        isFalse,
        reason: 'a community statement is a different message',
      );

      // A second run refuses and leaves the keys alone.
      final before = File(p.join(keyDir, kMlDsa65KeyFile)).readAsBytesSync();
      expect(
        await runKeysGenerate(
          outDir: keyDir,
          opensslPath: _caps.executable,
          environment: const {},
          out_: StringBuffer(),
          err_: StringBuffer(),
        ),
        65,
      );
      expect(File(p.join(keyDir, kMlDsa65KeyFile)).readAsBytesSync(), before);
    }, skip: skipUnlessPq(_caps));
  });
}
