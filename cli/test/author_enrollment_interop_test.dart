// `hqplugin keys enroll` against the real thing: real OpenSSL keys from `keys
// generate`, and the registry's own scripts (scripts/check-authors.mjs,
// scripts/author-cert.mjs) as the judge.
//
//  - The enrollment the CLI writes is accepted by `check-authors.mjs`, and its
//    statement is byte for byte what `author-cert.mjs statement` prints.
//  - The same keys enrolled a second time, or a prefix that overlaps an
//    enrolled one, are refused by the registry's check (the CLI relays it).
//  - A file whose content was changed after signing is refused too, so the
//    registry check is not vacuous.
//
// Needs node, the registry checkout (HELLOHQ_PLUGIN_REGISTRY_DIR;
// HELLOHQ_REQUIRE_REGISTRY=1 fails instead of skipping) and OpenSSL 3.5+ for
// the ML-DSA key (HQPLUGIN_REQUIRE_OPENSSL_PQ=1 fails instead of skipping).
import 'dart:convert';
import 'dart:io';

import 'package:hqplugin/src/author_enrollment.dart';
import 'package:hqplugin/src/author_signing.dart';
import 'package:hqplugin/src/keys_cmd.dart';
import 'package:hqplugin/src/keys_enroll.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

import 'support/author_test_support.dart';

final _caps = detectOpenSsl();

String? _skipPq() => skipUnlessPq(_caps);

String? _skipRegistry() {
  final pq = _skipPq();
  if (pq != null) return pq;
  final reg = registryCheckout();
  if (reg == null) {
    return requireRegistry
        ? null
        : 'plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)';
  }
  if (!File(p.join(reg.path, 'scripts', 'check-authors.mjs')).existsSync()) {
    return requireRegistry
        ? null
        : 'this plugin-registry checkout has no scripts/check-authors.mjs';
  }
  return hasNode ? null : 'node is not installed';
}

/// Keys made by `keys generate` with the real openssl.
Future<Directory> _newKeys() async {
  final dir = tempDir('hqplugin_enroll_keys_');
  final err = StringBuffer();
  final code = await runKeysGenerate(
    outDir: dir.path,
    opensslPath: _caps.executable,
    environment: const {},
    out_: StringBuffer(),
    err_: err,
  );
  expect(code, 0, reason: '$err');
  return dir;
}

/// The outcome of one `keys enroll`.
class _Run {
  _Run(this.code, this.out, this.err, this.file);
  final int code;
  final String out;
  final String err;
  final File file;
  Map<String, dynamic> get record =>
      jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

Future<_Run> _enroll(
  Directory keys, {
  required Directory work,
  String slug = 'acme-labs',
  List<String> prefixes = const ['com.acme.'],
  String subject = '4242',
  Directory? registry,
}) async {
  final out = StringBuffer();
  final err = StringBuffer();
  final code = await runKeysEnroll(
    keysDir: keys.path,
    slug: slug,
    prefixes: prefixes,
    oidcSubject: subject,
    opensslPath: _caps.executable,
    registryDir: registry?.path,
    environment: const {},
    currentDirectory: work.path,
    out_: out,
    err_: err,
  );
  return _Run(code, '$out', '$err', File(p.join(work.path, '$slug.json')));
}

void _copyTree(Directory from, Directory to) {
  to.createSync(recursive: true);
  for (final e in from.listSync(followLinks: false)) {
    final name = p.basename(e.path);
    if (e is File) {
      e.copySync(p.join(to.path, name));
    } else if (e is Directory) {
      _copyTree(e, Directory(p.join(to.path, name)));
    }
  }
}

/// A registry directory: the real scripts and the given enrolled authors
/// (file name to content).
Directory _registryWith(Map<String, String> authors) {
  final dir = tempDir('hqplugin_enroll_reg_');
  _copyTree(
    Directory(p.join(registryRoot().path, 'scripts')),
    Directory(p.join(dir.path, 'scripts')),
  );
  final dest = Directory(p.join(dir.path, 'authors'))..createSync();
  authors.forEach(
    (name, text) => File(p.join(dest.path, name)).writeAsStringSync(text),
  );
  return dir;
}

/// `node scripts/check-authors.mjs <dir>` of the real registry, on a directory
/// holding just [files] under authors/.
Future<ProcessResult> _registryCheck(Map<String, String> files) {
  final dir = tempDir('hqplugin_enroll_check_');
  final authors = Directory(p.join(dir.path, 'authors'))..createSync();
  files.forEach(
    (name, text) => File(p.join(authors.path, name)).writeAsStringSync(text),
  );
  return runNode([
    p.join(registryRoot().path, 'scripts', 'check-authors.mjs'),
    dir.path,
  ]);
}

void main() {
  group('the registry accepts what keys enroll writes', () {
    test(
      'check-authors.mjs is happy and the statement is the registry\'s',
      () async {
        final keys = await _newKeys();
        final work = tempDir('hqplugin_enroll_work_');
        final registry = _registryWith({});
        final r = await _enroll(keys, work: work, registry: registry);
        expect(r.code, 0, reason: r.err);
        expect(r.out, contains('authors/ is sound'));
        expect(r.out, contains('checked with 0 other enrolled authors'));
        expect(r.file.existsSync(), isTrue);

        // The registry's own check, run independently of the CLI.
        final check = await _registryCheck({
          'acme-labs.json': r.file.readAsStringSync(),
        });
        expect('${check.stdout}${check.stderr}', contains('authors/ is sound'));
        expect(check.exitCode, 0);

        // The Dart statement is byte for byte the registry's.
        final stmt = await runNode([
          p.join(registryRoot().path, 'scripts', 'author-cert.mjs'),
          'statement',
          r.file.path,
        ], binaryStdout: true);
        expect(stmt.exitCode, 0, reason: '${stmt.stderr}');
        expect(enrollmentStatement(r.record), stmt.stdout as List<int>);

        // It is the keys' own public halves, and the proof is 64 bytes.
        final tool = OpenSslTool(
          run: (e, a) => Process.run(e, a),
          executable: _caps.executable,
        );
        final keysJson =
            jsonDecode(
                  File(p.join(keys.path, kPublicKeysFile)).readAsStringSync(),
                )
                as Map;
        expect(r.record['public_keys'], keysJson);
        expect(
          base64.decode((r.record['proof'] as Map)['ed25519'] as String),
          hasLength(64),
        );
        expect(
          await tool.verifyWithPublicKey(
            HybridAlgorithm.ed25519,
            base64.decode(keysJson['ed25519'] as String),
            stmt.stdout as List<int>,
            base64.decode((r.record['proof'] as Map)['ed25519'] as String),
          ),
          isTrue,
        );

        // No private material in the file or the output.
        for (final f in [kEd25519KeyFile, kMlDsa65KeyFile]) {
          final body = File(
            p.join(keys.path, f),
          ).readAsLinesSync().where((l) => l.isNotEmpty && !l.startsWith('-'));
          for (final line in body) {
            expect(r.file.readAsStringSync(), isNot(contains(line)));
            expect('${r.out}${r.err}', isNot(contains(line)));
          }
        }
        expect('${r.out}${r.err}', isNot(contains('PRIVATE KEY')));
        // The checkout it was checked against is untouched.
        expect(
          File(p.join(registry.path, 'authors', 'acme-labs.json')).existsSync(),
          isFalse,
        );
      },
      skip: _skipRegistry(),
    );

    test('against the real registry checkout, calvin is counted', () async {
      final keys = await _newKeys();
      final work = tempDir('hqplugin_enroll_work_');
      final r = await _enroll(keys, work: work, registry: registryRoot());
      expect(r.code, 0, reason: r.err);
      expect(r.out, contains('checked with'));
      expect(r.out, contains('other enrolled author'));
      expect(r.out, isNot(contains('checked with 0 ')));
    }, skip: _skipRegistry());

    test(
      'unsorted prefixes are sorted into what the registry requires',
      () async {
        final keys = await _newKeys();
        final work = tempDir('hqplugin_enroll_work_');
        final registry = _registryWith({});
        // "-" sorts before "." (as in JavaScript), so com.acme-labs. comes first.
        final r = await _enroll(
          keys,
          work: work,
          prefixes: const ['org.acme.', 'com.acme.', 'com.acme-labs.'],
          registry: registry,
        );
        // com.acme-labs. and com.acme. do not overlap ("com.acme." is not a
        // prefix of "com.acme-labs."), so the registry takes all three.
        expect(r.code, 0, reason: r.err);
        expect(r.record['id_prefixes'], [
          'com.acme-labs.',
          'com.acme.',
          'org.acme.',
        ]);
        expect(r.out, contains('were sorted for you'));
        final check = await _registryCheck({
          'acme-labs.json': r.file.readAsStringSync(),
        });
        expect(check.exitCode, 0, reason: '${check.stdout}');
      },
      skip: _skipRegistry(),
    );
  });

  group('the registry refuses what must not be enrolled', () {
    test('the same keys under a second slug', () async {
      final keys = await _newKeys();
      final work = tempDir('hqplugin_enroll_work_');
      final first = await _enroll(
        keys,
        work: work,
        registry: _registryWith({}),
      );
      expect(first.code, 0, reason: first.err);

      final registry = _registryWith({
        'acme-labs.json': first.file.readAsStringSync(),
      });
      final work2 = tempDir('hqplugin_enroll_work2_');
      final second = await _enroll(
        keys,
        work: work2,
        slug: 'other-labs',
        prefixes: const ['org.other.'],
        subject: '7',
        registry: registry,
      );
      expect(second.code, 65, reason: '${second.out}${second.err}');
      expect(
        second.err,
        contains(
          'authors/other-labs.json: ed25519 key is already enrolled by '
          'authors/acme-labs.json',
        ),
      );
      expect(second.err, contains('ml-dsa-65 key is already enrolled'));
      expect(second.err, contains('no file was written'));
      expect(second.file.existsSync(), isFalse);
      expect(work2.listSync(), isEmpty);
    }, skip: _skipRegistry());

    test(
      'a prefix inside an enrolled prefix, and one that contains it',
      () async {
        final firstKeys = await _newKeys();
        final work = tempDir('hqplugin_enroll_work_');
        final first = await _enroll(
          firstKeys,
          work: work,
          prefixes: const ['com.acme.'],
          registry: _registryWith({}),
        );
        expect(first.code, 0, reason: first.err);
        final registry = _registryWith({
          'acme-labs.json': first.file.readAsStringSync(),
        });

        for (final (prefix, relation) in [
          ('com.acme.widgets.', 'overlaps "com.acme."'),
          ('com.acme.', 'overlaps "com.acme."'),
        ]) {
          final work2 = tempDir('hqplugin_enroll_work2_');
          final second = await _enroll(
            await _newKeys(),
            work: work2,
            slug: 'other-labs',
            prefixes: [prefix],
            subject: '7',
            registry: registry,
          );
          expect(
            second.code,
            65,
            reason: '$prefix: ${second.out}${second.err}',
          );
          expect(second.err, contains(relation), reason: prefix);
          expect(second.err, contains('of authors/acme-labs.json'));
          expect(second.file.existsSync(), isFalse);
          expect(work2.listSync(), isEmpty);
        }

        // The reverse: an enrolled narrow prefix blocks a wider claim.
        final narrow = await _enroll(
          await _newKeys(),
          work: tempDir('hqplugin_enroll_work3_'),
          slug: 'narrow-labs',
          prefixes: const ['org.narrow.sub.'],
          subject: '9',
          registry: _registryWith({}),
        );
        expect(narrow.code, 0, reason: narrow.err);
        final wide = await _enroll(
          await _newKeys(),
          work: tempDir('hqplugin_enroll_work4_'),
          slug: 'wide-labs',
          prefixes: const ['org.narrow.'],
          subject: '10',
          registry: _registryWith({
            'narrow-labs.json': narrow.file.readAsStringSync(),
          }),
        );
        expect(wide.code, 65, reason: wide.err);
        expect(wide.err, contains('overlaps "org.narrow.sub."'));
      },
      skip: _skipRegistry(),
    );

    test('a taken slug is refused before anything is signed', () async {
      final keys = await _newKeys();
      final work = tempDir('hqplugin_enroll_work_');
      final registry = _registryWith({'acme-labs.json': '{}'});
      final r = await _enroll(keys, work: work, registry: registry);
      expect(r.code, 65, reason: r.err);
      expect(r.err, contains('already enrolled'));
      expect(work.listSync(), isEmpty);
    }, skip: _skipRegistry());

    test(
      'the check is not vacuous: an edited file no longer verifies',
      () async {
        final keys = await _newKeys();
        final work = tempDir('hqplugin_enroll_work_');
        final r = await _enroll(keys, work: work, registry: _registryWith({}));
        expect(r.code, 0, reason: r.err);
        final text = r.file.readAsStringSync();

        // Another prefix after signing: the proof covers id_prefixes.
        final tampered = text.replaceFirst('com.acme.', 'com.other.');
        expect(tampered, isNot(text));
        var check = await _registryCheck({'acme-labs.json': tampered});
        expect(check.exitCode, 1);
        expect(
          '${check.stdout}',
          contains(
            'authors/acme-labs.json: proof.ed25519 does not verify over the '
            'enrollment statement',
          ),
        );

        // Another identity, same story.
        check = await _registryCheck({
          'acme-labs.json': text.replaceFirst('"4242"', '"4243"'),
        });
        expect(check.exitCode, 1);
        expect('${check.stdout}', contains('does not verify'));

        // And the unmodified file is fine.
        check = await _registryCheck({'acme-labs.json': text});
        expect(check.exitCode, 0, reason: '${check.stdout}');
      },
      skip: _skipRegistry(),
    );
  });

  group('the hqplugin binary', () {
    test('keys generate, then keys enroll, end to end', () async {
      final dir = tempDir('hqplugin_enroll_bin_');
      final keys = p.join(dir.path, 'keys');
      final env = {'HQPLUGIN_OPENSSL': _caps.executable};
      Future<ProcessResult> hqplugin(List<String> args) => Process.run(
        Platform.resolvedExecutable,
        ['run', 'bin/hqplugin.dart', ...args],
        workingDirectory: Directory.current.path,
        environment: env,
      );
      final gen = await hqplugin(['keys', 'generate', '--out', keys]);
      expect(gen.exitCode, 0, reason: '${gen.stdout}${gen.stderr}');
      expect('${gen.stdout}', contains('keys enroll'));

      final target = p.join(dir.path, 'mine.json');
      final enroll = await hqplugin([
        'keys',
        'enroll',
        '--keys',
        keys,
        '--slug',
        'acme-labs',
        '--prefix',
        'org.acme.',
        '--prefix',
        'com.acme.',
        '--oidc-subject',
        '4242',
        '--out',
        target,
      ]);
      expect(enroll.exitCode, 0, reason: '${enroll.stdout}${enroll.stderr}');
      final record =
          jsonDecode(File(target).readAsStringSync()) as Map<String, dynamic>;
      expect(record['id_prefixes'], ['com.acme.', 'org.acme.']);
      expect(enrollmentProblems(record, slugFromFile: 'acme-labs'), isEmpty);

      // The second run refuses to overwrite (65); a missing flag is usage (64).
      final again = await hqplugin([
        'keys',
        'enroll',
        '--keys',
        keys,
        '--slug',
        'acme-labs',
        '--prefix',
        'com.acme.',
        '--oidc-subject',
        '4242',
        '--out',
        target,
      ]);
      expect(again.exitCode, 65);
      final usage = await hqplugin(['keys', 'enroll', '--keys', keys]);
      expect(usage.exitCode, 64);
      expect('${usage.stderr}', contains('--slug'));
      expect('${usage.stderr}${usage.stdout}', isNot(contains('PRIVATE')));
    }, skip: _skipPq());
  });
}
