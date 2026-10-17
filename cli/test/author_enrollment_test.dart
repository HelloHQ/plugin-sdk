// `hqplugin keys enroll`: the enrollment statement and record rules (pinned to
// the registry's scripts/author-cert.mjs through a real, CI-verified
// enrollment), and the command against a fake openssl / gh / node (hermetic).
//
// The real-OpenSSL-and-registry end to end is author_enrollment_interop_test.
import 'dart:convert';
import 'dart:io';

import 'package:hqplugin/src/author_enrollment.dart';
import 'package:hqplugin/src/author_signing.dart';
import 'package:hqplugin/src/keys_enroll.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

import 'support/author_test_support.dart';
import 'support/calvin_enrollment.dart';

final _caps = detectOpenSsl();

String _statementText(Map<String, dynamic> author) =>
    utf8.decode(enrollmentStatement(author));

Map<String, dynamic> _record([Map<String, dynamic> overrides = const {}]) {
  final base = <String, dynamic>{
    'slug': 'acme-labs',
    'identity': {'oidc_issuer': 'https://github.com', 'oidc_subject': '4242'},
    'id_prefixes': ['com.acme.'],
    'public_keys': {
      'ed25519': base64.encode(List.filled(32, 7)),
      'ml-dsa-65': base64.encode(List.filled(1952, 9)),
    },
    'tier': 'author',
  };
  overrides.forEach((k, v) => v == null ? base.remove(k) : base[k] = v);
  return base;
}

/// A skip value for the tests that run the registry's node scripts.
String? _skipUnlessRegistryAndNode() {
  final reg = registryCheckout();
  if (reg == null) {
    return requireRegistry
        ? null
        : 'plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)';
  }
  return hasNode ? null : 'node is not installed';
}

void main() {
  group('the registry golden vector (authors/calvin.json)', () {
    test('the inline copy is a sound enrollment', () {
      expect(
        enrollmentProblems(calvinRecord(), slugFromFile: 'calvin'),
        isEmpty,
      );
    });

    test('the Dart statement is the registry statement (inline literal)', () {
      expect(_statementText(calvinRecord()), kCalvinStatement);
      expect(kCalvinStatement.split('\n'), hasLength(9));
      expect(utf8.decode(utf8.encode(kCalvinStatement)), kCalvinStatement);
    });

    test('an AuthorEnrollment writes calvin.json byte for byte', () {
      final calvin = calvinRecord();
      final keys = calvin['public_keys'] as Map;
      final enrollment = AuthorEnrollment(
        slug: 'calvin',
        oidcIssuer: 'https://github.com',
        oidcSubject: '95563',
        idPrefixes: const ['com.calvinx.'],
        ed25519PublicKey: base64.decode(keys['ed25519'] as String),
        mlDsa65PublicKey: base64.decode(keys['ml-dsa-65'] as String),
      );
      expect(enrollment.statement, utf8.encode(kCalvinStatement));
      expect(enrollment.toFileJson(base64.decode(kCalvinProof)), kCalvinFile);
    });

    test(
      'the real proof verifies over it under the real Ed25519 key',
      () async {
        final tool = OpenSslTool(
          run: (e, a) => Process.run(e, a),
          executable: _caps.executable,
        );
        final key = base64.decode(kCalvinEd25519);
        final proof = base64.decode(kCalvinProof);
        expect(
          await tool.verifyWithPublicKey(
            HybridAlgorithm.ed25519,
            key,
            utf8.encode(kCalvinStatement),
            proof,
          ),
          isTrue,
        );
        // Not vacuous: a statement without the id_prefixes line, one prefix
        // changed, or another slug does not verify under the same proof.
        for (final altered in [
          kCalvinStatement.replaceFirst('id_prefixes=com.calvinx.\n', ''),
          kCalvinStatement.replaceFirst('com.calvinx.', 'com.calvin.'),
          kCalvinStatement.replaceFirst('slug=calvin', 'slug=calvinx'),
        ]) {
          expect(altered, isNot(kCalvinStatement));
          expect(
            await tool.verifyWithPublicKey(
              HybridAlgorithm.ed25519,
              key,
              utf8.encode(altered),
              proof,
            ),
            isFalse,
            reason: altered.split('\n').take(3).join('|'),
          );
        }
      },
      skip: requirePq ? null : skipUnlessEd25519(_caps),
    );

    test('matches `author-cert.mjs statement` for the registry\'s own '
        'authors/calvin.json', () async {
      final reg = registryRoot();
      final file = File(p.join(reg.path, 'authors', 'calvin.json'));
      expect(file.existsSync(), isTrue, reason: 'no authors/calvin.json');
      final r = await runNode([
        p.join(reg.path, 'scripts', 'author-cert.mjs'),
        'statement',
        file.path,
      ], binaryStdout: true);
      expect(r.exitCode, 0, reason: '${r.stderr}');
      final real = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
      expect(enrollmentStatement(real), r.stdout as List<int>);
      expect(enrollmentProblems(real, slugFromFile: 'calvin'), isEmpty);
    }, skip: _skipUnlessRegistryAndNode());
  });

  group('the enrollment statement', () {
    test('is the version line then seven name=value lines, LF only', () {
      final text = _statementText(_record());
      final lines = text.split('\n');
      expect(lines.last, '');
      expect(text, isNot(contains('\r')));
      expect(lines.take(8).map((l) => l.split('=').first).toList(), [
        'hellohq-author-enrollment/v1',
        'slug',
        'oidc_issuer',
        'oidc_subject',
        'ed25519',
        'ml-dsa-65',
        'id_prefixes',
        'tier',
      ]);
    });

    test('joins the prefixes with a comma, in the order given', () {
      final text = _statementText(
        _record({
          'id_prefixes': ['com.acme.', 'com.acme-labs.', 'org.acme.'],
        }),
      );
      expect(
        text,
        contains('id_prefixes=com.acme.,com.acme-labs.,org.acme.\n'),
      );
    });

    test('absent id_prefixes is an empty list, as in the registry', () {
      expect(
        _statementText(_record({'id_prefixes': null})),
        contains('\nid_prefixes=\n'),
      );
    });

    test('a value with a line break is refused, naming the field', () {
      for (final (name, overrides) in <(String, Map<String, dynamic>)>[
        ('slug', {'slug': 'a\nb'}),
        (
          'oidc_issuer',
          {
            'identity': {'oidc_issuer': 'https://x\r', 'oidc_subject': '1'},
          },
        ),
        (
          'oidc_subject',
          {
            'identity': {'oidc_issuer': 'https://x', 'oidc_subject': '1\n2'},
          },
        ),
        ('tier', {'tier': 'author\n'}),
      ]) {
        expect(
          () => enrollmentStatement(_record(overrides)),
          throwsA(
            isA<StatementException>().having(
              (e) => e.message,
              'message',
              'enrollment statement: $name must be a single-line string',
            ),
          ),
          reason: name,
        );
      }
    });

    test('a missing field is refused like a line break', () {
      expect(
        () => enrollmentStatement(_record({'slug': null})),
        throwsA(isA<StatementException>()),
      );
      expect(
        () => enrollmentStatement(_record({'identity': null})),
        throwsA(isA<StatementException>()),
      );
    });

    test('the proof is not part of the statement', () {
      expect(
        _statementText(
          _record({
            'proof': {'ed25519': 'x'},
          }),
        ),
        _statementText(_record()),
      );
    });
  });

  group('slug rule', () {
    test('accepts 3 to 40 chars of a-z, 0-9 and inner hyphens', () {
      for (final s in [
        'abc',
        'acme-labs',
        'a1b',
        'a--b',
        'a${'b' * 38}c',
        'x9-y',
      ]) {
        expect(slugProblems(s), isEmpty, reason: s);
      }
      expect(('a${'b' * 38}c').length, 40);
    });

    test('rejects the rest', () {
      for (final s in [
        '',
        'a',
        'ab',
        'Acme',
        'acme_labs',
        '1abc',
        '-abc',
        'abc-',
        'abc.',
        'abc\n',
        'a${'b' * 39}c',
        'acme labs',
        'café',
      ]) {
        expect(slugProblems(s), isNotEmpty, reason: '"$s"');
      }
      expect(slugProblems(null), isNotEmpty);
      expect(slugProblems(5), isNotEmpty);
    });
  });

  group('prefix rules', () {
    test('accepts sorted, unique, dotted prefixes with two or more labels', () {
      for (final list in [
        ['com.acme.'],
        ['io.github.foo-bar.'],
        ['org.x1.'],
        ['com.acme-labs.', 'com.acme.', 'org.acme.'],
        ['com.hellohqx.'],
        ['a.b.'],
        [for (var i = 0; i < 8; i++) 'com.a${i}b.'],
      ]) {
        expect(prefixProblems(list), isEmpty, reason: '$list');
      }
    });

    test('1 to 8 prefixes', () {
      expect(prefixProblems(<String>[]), [
        'id_prefixes must be 1 to 8 prefixes',
      ]);
      expect(prefixProblems(null), ['id_prefixes must be 1 to 8 prefixes']);
      expect(prefixProblems('com.acme.'), isNotEmpty);
      expect(prefixProblems([for (var i = 0; i < 9; i++) 'com.a${i}b.']), [
        'id_prefixes must be 1 to 8 prefixes',
      ]);
    });

    test('each entry must be dotted and end in "."', () {
      for (final bad in [
        'com.acme',
        'Com.acme.',
        '.acme.',
        'com..acme.',
        'com.acme._x.',
        'com.ac me.',
        '1com.acme.',
        '',
        'com.acme.\n',
      ]) {
        expect(
          prefixProblems([bad]),
          contains('id_prefixes entries must be dotted prefixes ending in "."'),
          reason: '"$bad"',
        );
      }
      expect(prefixProblems([5]), isNotEmpty);
    });

    test('at least two labels', () {
      expect(prefixProblems(['acme.']), [
        'id_prefixes entry "acme." must have at least two labels',
      ]);
    });

    test('com.hellohq. and everything under it is reserved', () {
      for (final r in [
        'com.hellohq.',
        'com.hellohq.plugins.',
        'com.hellohq.a.',
      ]) {
        expect(prefixProblems([r]), [
          'id_prefixes entry "$r" is reserved for HelloHQ',
        ], reason: r);
      }
      // Not under it: a different label that merely starts the same.
      expect(prefixProblems(['com.hellohqx.']), isEmpty);
      expect(prefixProblems(['com.hello.']), isEmpty);
    });

    test('unique and sorted', () {
      const msg = 'id_prefixes must be unique and sorted';
      expect(prefixProblems(['com.acme.', 'com.acme.']), [msg]);
      expect(prefixProblems(['org.acme.', 'com.acme.']), [msg]);
      expect(prefixProblems(['com.acme.', 'org.acme.']), isEmpty);
      // Code-unit order, as JavaScript's sort(): "-" (0x2d) sorts before ".".
      expect(prefixProblems(['com.acme.', 'com.acme-labs.']), [msg]);
      expect(prefixProblems(['com.acme-labs.', 'com.acme.']), isEmpty);
    });
  });

  group('identity rules', () {
    test('a GitHub subject is the numeric account id', () {
      expect(identityProblems(kGitHubIssuer, '95563'), isEmpty);
      for (final bad in ['octocat', '95563x', '0', '007', '-1', ' 1', '1.5']) {
        expect(identityProblems(kGitHubIssuer, bad), isNotEmpty, reason: bad);
      }
      expect(isGitHubAccountId('1'), isTrue);
      expect(isGitHubAccountId('12345678901'), isTrue);
      expect(isGitHubAccountId(''), isFalse);
    });

    test('other issuers take any single-line subject', () {
      expect(
        identityProblems('https://accounts.example.com', 'user-abc_123'),
        isEmpty,
      );
    });

    test('the issuer must be an https URL', () {
      for (final bad in [
        'http://github.com',
        'github.com',
        '',
        'ftp://x',
        'https:/x',
      ]) {
        expect(identityProblems(bad, '1'), isNotEmpty, reason: bad);
      }
      expect(
        identityProblems('http://x', '1'),
        contains('identity.oidc_issuer must be an https URL'),
      );
    });

    test('both are required and single-line', () {
      expect(identityProblems(null, null), hasLength(2));
      expect(identityProblems('https://x', ''), isNotEmpty);
      expect(identityProblems('https://x', 'a\nb'), isNotEmpty);
      expect(identityProblems('https://x\n', 'a'), isNotEmpty);
    });

    test('GitHub logins', () {
      for (final ok in ['octocat', 'a', 'a-b', 'A1', 'a' * 39]) {
        expect(isGitHubLogin(ok), isTrue, reason: ok);
      }
      for (final bad in [
        '',
        '-a',
        'a-',
        'a/b',
        '../x',
        'a b',
        'a' * 40,
        'a_b',
      ]) {
        expect(isGitHubLogin(bad), isFalse, reason: bad);
      }
    });
  });

  group('record rules', () {
    test('a sound record has no problems', () {
      expect(enrollmentProblems(_record(), slugFromFile: 'acme-labs'), isEmpty);
    });

    test('public keys must be the right size and canonical base64', () {
      Map<String, dynamic> withKeys(String ed, String ml) => _record({
        'public_keys': {'ed25519': ed, 'ml-dsa-65': ml},
      });
      final ed = base64.encode(List.filled(32, 1));
      final ml = base64.encode(List.filled(1952, 1));
      expect(enrollmentProblems(withKeys(ed, ml)), isEmpty);
      expect(
        enrollmentProblems(withKeys(base64.encode(List.filled(31, 1)), ml)),
        ['public_keys.ed25519 must be 32 bytes, base64'],
      );
      expect(
        enrollmentProblems(withKeys(ed, base64.encode(List.filled(1951, 1)))),
        ['public_keys.ml-dsa-65 must be 1952 bytes, base64'],
      );
      expect(enrollmentProblems(withKeys('$ed=', ml)), isNotEmpty);
      expect(enrollmentProblems(withKeys('not base64!', ml)), isNotEmpty);
      // 32 bytes whose base64 ends in a non-canonical character.
      expect(
        enrollmentProblems(withKeys('${ed.substring(0, 42)}B=', ml)),
        isNotEmpty,
      );
    });

    test('tier must be author, no unknown keys, slug must match the file', () {
      expect(enrollmentProblems(_record({'tier': 'verified'})), [
        'tier must be "author"',
      ]);
      expect(
        enrollmentProblems(
          _record({
            'public_keys': {
              'ed25519': base64.encode(List.filled(32, 7)),
              'ml-dsa-65': base64.encode(List.filled(1952, 9)),
              'rsa': 'x',
            },
          }),
        ),
        ['unknown public_keys: rsa'],
      );
      expect(enrollmentProblems(_record(), slugFromFile: 'other'), [
        'slug "acme-labs" must match the file name "other"',
      ]);
      expect(enrollmentProblems('x'), ['not an object']);
    });
  });

  group('keys enroll with a fake openssl, gh and node', () {
    late _Env env;
    late StringBuffer out;
    late StringBuffer err;
    late Directory work;
    late Directory keys;

    setUp(() {
      env = _Env();
      out = StringBuffer();
      err = StringBuffer();
      work = tempDir('hqplugin_enroll_work_');
      keys = FakeOpenSsl.keyDir();
    });

    Future<int> enroll({
      String? keysDir,
      String? slug = 'acme-labs',
      List<String> prefixes = const ['com.acme.'],
      String? githubUser,
      String? oidcSubject = '4242',
      String? oidcIssuer,
      String? outPath,
      String? registryDir,
      Map<String, String> environment = const {},
    }) => runKeysEnroll(
      keysDir: keysDir ?? keys.path,
      slug: slug,
      prefixes: prefixes,
      githubUser: githubUser,
      oidcSubject: githubUser == null ? oidcSubject : null,
      oidcIssuer: oidcIssuer,
      outPath: outPath,
      registryDir: registryDir,
      environment: environment,
      currentDirectory: work.path,
      out_: out,
      err_: err,
      runner: env.call,
    );

    File written([String name = 'acme-labs.json']) =>
        File(p.join(work.path, name));

    Map<String, dynamic> readRecord([String name = 'acme-labs.json']) =>
        jsonDecode(written(name).readAsStringSync()) as Map<String, dynamic>;

    group('usage (exit 64)', () {
      test('--slug is required', () async {
        expect(await enroll(slug: null), 64);
        expect('$err', contains('--slug'));
        expect(await enroll(slug: ''), 64);
      });

      test('at least one --prefix', () async {
        expect(await enroll(prefixes: const []), 64);
        expect('$err', contains('--prefix'));
      });

      test('an identity is required, and only one kind', () async {
        expect(await enroll(oidcSubject: null), 64);
        expect('$err', contains('--github-user'));
        err.clear();
        expect(
          await runKeysEnroll(
            keysDir: keys.path,
            slug: 'acme-labs',
            prefixes: const ['com.acme.'],
            githubUser: 'octocat',
            oidcSubject: '1',
            currentDirectory: work.path,
            out_: out,
            err_: err,
            runner: env.call,
            environment: const {},
          ),
          64,
        );
        expect('$err', contains('not both'));
      });

      test('--oidc-issuer goes with --oidc-subject', () async {
        expect(
          await enroll(githubUser: 'octocat', oidcIssuer: 'https://x.example'),
          64,
        );
        expect('$err', contains('--oidc-issuer'));
      });

      test(
        'the key directory is required: --keys or the environment',
        () async {
          final code = await runKeysEnroll(
            slug: 'acme-labs',
            prefixes: const ['com.acme.'],
            oidcSubject: '4242',
            environment: const {},
            currentDirectory: work.path,
            out_: out,
            err_: err,
            runner: env.call,
          );
          expect(code, 64);
          expect('$err', contains('HQPLUGIN_AUTHOR_KEY_DIR'));
          expect(env.openssl.calls, isEmpty);
        },
      );

      test('HQPLUGIN_AUTHOR_KEY_DIR stands in for --keys', () async {
        final code = await runKeysEnroll(
          slug: 'acme-labs',
          prefixes: const ['com.acme.'],
          oidcSubject: '4242',
          environment: {'HQPLUGIN_AUTHOR_KEY_DIR': keys.path},
          currentDirectory: work.path,
          out_: out,
          err_: err,
          runner: env.call,
        );
        expect(code, 0, reason: '$err');
        expect(written().existsSync(), isTrue);
      });
    });

    group('the file it writes', () {
      test('is the registry layout, with a verifying proof', () async {
        expect(await enroll(), 0, reason: '$err');
        final text = written().readAsStringSync();
        expect(text, endsWith('}\n'));
        final r = jsonDecode(text) as Map<String, dynamic>;
        expect(r.keys, [
          'slug',
          'identity',
          'id_prefixes',
          'public_keys',
          'tier',
          'proof',
        ]);
        expect((r['identity'] as Map).keys, ['oidc_issuer', 'oidc_subject']);
        expect(r['identity'], {
          'oidc_issuer': 'https://github.com',
          'oidc_subject': '4242',
        });
        expect((r['public_keys'] as Map).keys, ['ed25519', 'ml-dsa-65']);
        expect((r['proof'] as Map).keys, ['ed25519']);
        expect(r['tier'], 'author');
        expect(text, contains('  "id_prefixes": [\n    "com.acme."\n  ],'));
        expect(enrollmentProblems(r, slugFromFile: 'acme-labs'), isEmpty);

        // The proof verifies over the statement under the file's own key.
        final tool = OpenSslTool(run: env.openssl.call);
        expect(
          await tool.verifyWithPublicKey(
            HybridAlgorithm.ed25519,
            base64.decode((r['public_keys'] as Map)['ed25519'] as String),
            enrollmentStatement(r),
            base64.decode((r['proof'] as Map)['ed25519'] as String),
          ),
          isTrue,
        );
        // ...and not over a different statement.
        expect(
          await tool.verifyWithPublicKey(
            HybridAlgorithm.ed25519,
            base64.decode((r['public_keys'] as Map)['ed25519'] as String),
            enrollmentStatement({
              ...r,
              'id_prefixes': ['com.other.'],
            }),
            base64.decode((r['proof'] as Map)['ed25519'] as String),
          ),
          isFalse,
        );
      });

      test('has the public keys openssl derives, nothing private', () async {
        expect(await enroll(), 0, reason: '$err');
        final r = readRecord();
        final tool = OpenSslTool(run: env.openssl.call);
        final ed = await tool.rawPublicKey(
          HybridAlgorithm.ed25519,
          p.join(keys.path, kEd25519KeyFile),
        );
        final ml = await tool.rawPublicKey(
          HybridAlgorithm.mlDsa65,
          p.join(keys.path, kMlDsa65KeyFile),
        );
        expect((r['public_keys'] as Map)['ed25519'], base64.encode(ed));
        expect((r['public_keys'] as Map)['ml-dsa-65'], base64.encode(ml));
        final everything = '${written().readAsStringSync()}$out$err';
        expect(everything, isNot(contains(kFakeSecretMarker)));
        expect(everything, isNot(contains('PRIVATE')));
        expect(everything, isNot(contains('BEGIN')));
        final privateBody = File(
          p.join(keys.path, kEd25519KeyFile),
        ).readAsLinesSync().where((l) => l.isNotEmpty && !l.startsWith('-'));
        for (final line in privateBody) {
          expect(everything, isNot(contains(line)));
        }
      });

      test('prefixes are sorted for the author, and they are told', () async {
        expect(
          await enroll(prefixes: const ['org.acme.', 'com.acme.', 'com.a-b.']),
          0,
          reason: '$err',
        );
        expect(readRecord()['id_prefixes'], [
          'com.a-b.',
          'com.acme.',
          'org.acme.',
        ]);
        expect('$out', contains('sorted'));
        expect('$out', contains('org.acme., com.acme., com.a-b.'));
        // The proof covers the sorted list.
        expect(
          enrollmentProblems(readRecord(), slugFromFile: 'acme-labs'),
          isEmpty,
        );
      });

      test('sorted input is not reported as re-sorted', () async {
        expect(await enroll(prefixes: const ['com.acme.', 'org.acme.']), 0);
        expect('$out', isNot(contains('were sorted')));
      });

      test('default path is ./<slug>.json in the current directory', () async {
        expect(await enroll(), 0, reason: '$err');
        expect(written().existsSync(), isTrue);
        expect('$out', contains(p.join(work.path, 'acme-labs.json')));
      });

      test('--out: relative paths are under the current directory', () async {
        Directory(p.join(work.path, 'sub')).createSync();
        expect(await enroll(outPath: p.join('sub', 'mine.json')), 0);
        expect(
          File(p.join(work.path, 'sub', 'mine.json')).existsSync(),
          isTrue,
        );
        expect(written().existsSync(), isFalse);
        expect('$out', contains('rename it'));
      });

      test('--out: a missing directory is exit 65', () async {
        expect(await enroll(outPath: p.join('nope', 'x.json')), 65);
        expect('$err', contains('does not exist'));
      });

      test(
        'the PR instructions and the custody reminder are printed',
        () async {
          expect(await enroll(), 0);
          final shown = '$out';
          expect(shown, contains('https://github.com/HelloHQ/plugin-registry'));
          expect(shown, contains('authors/acme-labs.json'));
          expect(shown, contains('one author per PR'));
          expect(shown, contains('maintainer reviews'));
          expect(shown, contains('dispatch the signer'));
          expect(shown, contains('private keys never leave this machine'));
          expect(shown, contains('hqplugin publish --sign-key ${keys.path}'));
          expect(shown, contains('registry CI'));
        },
      );
    });

    group('refusing to overwrite (exit 65)', () {
      test('an existing file is never replaced, nothing is run', () async {
        written().writeAsStringSync('mine');
        expect(await enroll(), 65);
        expect('$err', contains('refusing to overwrite'));
        expect(written().readAsStringSync(), 'mine');
        expect(env.openssl.calls, isEmpty);
        expect(env.ghCalls, isEmpty);
      });

      test('also for an explicit --out', () async {
        final f = File(p.join(work.path, 'x.json'))..writeAsStringSync('mine');
        expect(await enroll(outPath: f.path), 65);
        expect(f.readAsStringSync(), 'mine');
      });
    });

    group('validation happens before anything is signed (exit 65)', () {
      Future<void> refused(Future<int> Function() body, Pattern message) async {
        expect(await body(), 65);
        expect('$err', contains(message));
        expect(env.openssl.calls, isEmpty, reason: 'openssl must not run');
        expect(env.ghCalls, isEmpty);
        expect(work.listSync(), isEmpty);
      }

      test('slug', () async {
        for (final bad in ['Acme', 'ab', 'acme_labs', '-abc']) {
          err.clear();
          await refused(() => enroll(slug: bad), 'slug must be 3-40 chars');
        }
      });

      test('prefixes: shape, labels, reserved, count', () async {
        await refused(
          () => enroll(prefixes: const ['com.acme']),
          'must be dotted prefixes ending in "."',
        );
        err.clear();
        await refused(
          () => enroll(prefixes: const ['acme.']),
          'must have at least two labels',
        );
        err.clear();
        await refused(
          () => enroll(prefixes: const ['com.hellohq.']),
          '"com.hellohq." is reserved for HelloHQ',
        );
        err.clear();
        await refused(
          () => enroll(prefixes: const ['com.hellohq.sub.']),
          'is reserved for HelloHQ',
        );
        err.clear();
        await refused(
          () => enroll(prefixes: [for (var i = 0; i < 9; i++) 'com.a${i}b.']),
          'must be 1 to 8 prefixes',
        );
      });

      test('duplicate prefixes', () async {
        await refused(
          () => enroll(prefixes: const ['com.acme.', 'com.acme.']),
          'unique',
        );
      });

      test('every problem is listed, not just the first', () async {
        expect(await enroll(slug: 'X', prefixes: const ['com.hellohq.']), 65);
        expect('$err', contains('slug must be'));
        expect('$err', contains('reserved'));
      });

      test('a login is not a GitHub subject', () async {
        await refused(
          () => enroll(oidcSubject: 'octocat'),
          'numeric GitHub account id',
        );
      });

      test('--oidc-issuer must be https', () async {
        await refused(
          () => enroll(oidcIssuer: 'http://sso.example.com'),
          'must be an https URL',
        );
      });

      test('a non-GitHub issuer takes any subject', () async {
        expect(
          await enroll(
            oidcIssuer: 'https://sso.example.com',
            oidcSubject: 'user-abc',
          ),
          0,
          reason: '$err',
        );
        expect(readRecord()['identity'], {
          'oidc_issuer': 'https://sso.example.com',
          'oidc_subject': 'user-abc',
        });
      });

      test('a malformed --github-user never reaches gh', () async {
        for (final bad in ['../orgs', 'a b', '-x', 'a/b', '']) {
          err.clear();
          await refused(() => enroll(githubUser: bad), 'is not a GitHub login');
        }
      });
    });

    group('--github-user (gh)', () {
      test('resolves the numeric id and enrolls it under github.com', () async {
        env.ghUsers['octocat'] = '583231';
        env.ghLogin = 'octocat';
        expect(await enroll(githubUser: 'octocat'), 0, reason: '$err');
        expect(env.ghCalls, [
          ['api', 'users/octocat', '--jq', '.id'],
          ['api', 'user', '--jq', '.login'],
        ]);
        expect(readRecord()['identity'], {
          'oidc_issuer': 'https://github.com',
          'oidc_subject': '583231',
        });
        expect('$out', contains('account id 583231'));
        expect('$err', isEmpty);
      });

      test('a leading @ is accepted', () async {
        env.ghUsers['octocat'] = '583231';
        env.ghLogin = 'octocat';
        expect(await enroll(githubUser: '@octocat'), 0, reason: '$err');
        expect(env.ghCalls.first, ['api', 'users/octocat', '--jq', '.id']);
      });

      test(
        'warns, but does not fail, when gh is logged in as someone else',
        () async {
          env.ghUsers['octocat'] = '583231';
          env.ghLogin = 'hubot';
          expect(await enroll(githubUser: 'octocat'), 0, reason: '$err');
          expect('$err', contains('warning'));
          expect('$err', contains('"hubot"'));
          expect('$err', contains('"octocat"'));
          expect('$err', contains('someone else'));
          expect(
            readRecord()['identity'],
            containsPair('oidc_subject', '583231'),
          );
        },
      );

      test('the login comparison ignores case', () async {
        env.ghUsers['Octocat'] = '583231';
        env.ghLogin = 'octocat';
        expect(await enroll(githubUser: 'Octocat'), 0, reason: '$err');
        expect('$err', isEmpty);
      });

      test('warns when gh cannot say who it is logged in as', () async {
        env.ghUsers['octocat'] = '583231';
        env.ghLogin = null;
        expect(await enroll(githubUser: 'octocat'), 0, reason: '$err');
        expect('$err', contains('could not tell'));
      });

      test('a non-numeric id from gh is refused, before signing', () async {
        env.ghUsers['octocat'] = 'MDQ6VXNlcjE=';
        expect(await enroll(githubUser: 'octocat'), 65);
        expect('$err', contains('not a numeric GitHub account id'));
        expect(env.openssl.calls, isEmpty);
        expect(work.listSync(), isEmpty);
      });

      test('an unknown user is exit 65', () async {
        expect(await enroll(githubUser: 'nobody-here'), 65);
        expect('$err', contains('no user "nobody-here"'));
        expect(work.listSync(), isEmpty);
      });

      test('gh missing is exit 69 and says what to do instead', () async {
        env.ghInstalled = false;
        expect(await enroll(githubUser: 'octocat'), 69);
        expect('$err', contains('gh (the GitHub CLI) was not found'));
        expect('$err', contains('--oidc-subject'));
        expect(work.listSync(), isEmpty);
      });

      test('gh failing for another reason is exit 69', () async {
        env.ghUsers['octocat'] = '1';
        env.ghFailure =
            'To get started with GitHub CLI, please run: gh auth login';
        expect(await enroll(githubUser: 'octocat'), 69);
        expect('$err', contains('gh auth login'));
        expect(work.listSync(), isEmpty);
      });

      test('--oidc-subject needs no gh at all', () async {
        env.ghInstalled = false;
        expect(await enroll(oidcSubject: '4242'), 0, reason: '$err');
        expect(env.ghCalls, isEmpty);
      });
    });

    group('keys and openssl', () {
      test('a missing key directory is exit 66', () async {
        expect(await enroll(keysDir: p.join(work.path, 'no-such-keys')), 66);
        expect('$err', contains('keys generate'));
        expect(written().existsSync(), isFalse);
      });

      test('a missing key file is exit 66', () async {
        File(p.join(keys.path, kMlDsa65KeyFile)).deleteSync();
        expect(await enroll(), 66);
        expect('$err', contains(kMlDsa65KeyFile));
        expect(written().existsSync(), isFalse);
      });

      test('a file that is not a key is exit 65', () async {
        File(p.join(keys.path, kEd25519KeyFile)).writeAsStringSync('nope');
        expect(await enroll(), 65);
        expect(written().existsSync(), isFalse);
      });

      test(
        'a swapped pair (ML-DSA key as the Ed25519 file) is exit 65',
        () async {
          File(
            p.join(keys.path, kMlDsa65KeyFile),
          ).copySync(p.join(keys.path, kEd25519KeyFile));
          expect(await enroll(), 65);
          expect('$err', contains('not an ed25519 private key'));
          expect(written().existsSync(), isFalse);
        },
      );

      test('openssl missing or too old is exit 69', () async {
        env.openssl.installed = false;
        expect(await enroll(), 69);
        expect('$err', contains('was not found'));
        err.clear();
        env.openssl
          ..installed = true
          ..pq = false;
        expect(await enroll(), 69);
        expect('$err', contains('3.5 or newer'));
        expect(written().existsSync(), isFalse);
      });

      test('--openssl and HQPLUGIN_OPENSSL choose the binary', () async {
        expect(
          await runKeysEnroll(
            keysDir: keys.path,
            slug: 'acme-labs',
            prefixes: const ['com.acme.'],
            oidcSubject: '4242',
            opensslPath: '/opt/ossl/bin/openssl',
            environment: {'HQPLUGIN_OPENSSL': '/ignored/openssl'},
            currentDirectory: work.path,
            out_: out,
            err_: err,
            runner: env.call,
          ),
          0,
          reason: '$err',
        );
        expect(env.openssl.exes.toSet(), {'/opt/ossl/bin/openssl'});
        expect(
          await runKeysEnroll(
            keysDir: keys.path,
            slug: 'other-labs',
            prefixes: const ['com.other.'],
            oidcSubject: '4242',
            environment: {'HQPLUGIN_OPENSSL': '/from/env/openssl'},
            currentDirectory: work.path,
            out_: out,
            err_: err,
            runner: env.call,
          ),
          0,
          reason: '$err',
        );
        expect(env.openssl.exes.last, '/from/env/openssl');
      });

      test('the proof is one Ed25519 signature, made and checked as the '
          'registry does', () async {
        expect(await enroll(), 0, reason: '$err');
        final calls = env.openssl.calls;
        final ed = p.absolute(p.join(keys.path, kEd25519KeyFile));
        final ml = p.absolute(p.join(keys.path, kMlDsa65KeyFile));
        final signs = [
          for (final c in calls)
            if (c.first == 'pkeyutl' && c[1] == '-sign') c,
        ];
        // The two key probes of OpenSslHybridSigner.open, then the proof.
        expect(signs, hasLength(3));
        expect(signs.last, [
          'pkeyutl',
          '-sign',
          '-inkey',
          ed,
          '-rawin',
          '-in',
          signs.last[6],
          '-out',
          signs.last[8],
        ]);
        expect(p.basename(signs.last[6]), 'msg');
        expect(p.basename(signs.last[8]), 'sig');
        // Raw (pure) Ed25519, never a digest signature.
        expect(signs.last, contains('-rawin'));
        expect(signs.last, isNot(contains('-digest')));
        expect(signs.last, isNot(contains(ml)));
        // The last thing openssl is asked is to verify against a public key
        // file, not a private one.
        final last = calls.last;
        expect(last.take(4), ['pkeyutl', '-verify', '-pubin', '-inkey']);
        expect(last, contains('-rawin'));
        expect(p.basename(last[4]), 'pub.pem');
        // No key is generated, copied or exported by enroll.
        for (final c in calls) {
          if (c.first == 'genpkey') {
            expect(c[2], 'ML-DSA-65', reason: 'only the preflight probe');
            expect(p.isWithin(keys.path, c.last), isFalse);
          }
          expect(c, isNot(contains('-text')));
          expect(c, isNot(contains('-noout')));
        }
        // Temporary message/signature directories are gone.
        for (final d in env.openssl.tempDirs) {
          expect(Directory(d).existsSync(), isFalse, reason: d);
        }
      });

      test(
        'a proof that does not verify is exit 70 and leaves no file',
        () async {
          // Verifications: the two key probes, then the proof check.
          env.openssl.verifyQueue.addAll([true, true, false]);
          expect(await enroll(), 70);
          expect('$err', contains('did not check out'));
          expect('$err', contains('does not verify'));
          expect(written().existsSync(), isFalse);
          expect(work.listSync(), isEmpty);
        },
      );

      test('a failure after the check leaves nothing half written', () async {
        // Same, for a different default path.
        env.openssl.verifyQueue.addAll([true, true, false]);
        expect(await enroll(outPath: 'custom.json'), 70);
        expect(work.listSync(), isEmpty);
      });
    });

    group('--registry-dir (the registry\'s own check)', () {
      late Directory registry;
      setUp(() {
        registry = tempDir('hqplugin_enroll_registry_');
        Directory(
          p.join(registry.path, 'scripts', 'lib'),
        ).createSync(recursive: true);
        File(
          p.join(registry.path, 'scripts', 'check-authors.mjs'),
        ).writeAsStringSync('// stand-in');
        File(
          p.join(registry.path, 'scripts', 'author-cert.mjs'),
        ).writeAsStringSync('// stand-in');
        File(
          p.join(registry.path, 'scripts', 'lib', 'x.mjs'),
        ).writeAsStringSync('// nested');
        Directory(p.join(registry.path, 'authors')).createSync();
        File(
          p.join(registry.path, 'authors', 'calvin.json'),
        ).writeAsStringSync(kCalvinFile);
        File(
          p.join(registry.path, 'authors', 'README.md'),
        ).writeAsStringSync('not json');
      });

      test('runs check-authors.mjs on a scratch copy and reports it', () async {
        late String scratchRoot;
        late Set<String> authors;
        late List<String> scripts;
        late String newFile;
        env.node = (args) {
          scratchRoot = args[1];
          expect(args[0], p.join(scratchRoot, 'scripts', 'check-authors.mjs'));
          authors = {
            for (final f in Directory(
              p.join(scratchRoot, 'authors'),
            ).listSync())
              p.basename(f.path),
          };
          scripts = [
            for (final f in Directory(
              p.join(scratchRoot, 'scripts'),
            ).listSync(recursive: true).whereType<File>())
              p.relative(f.path, from: p.join(scratchRoot, 'scripts')),
          ]..sort();
          newFile = File(
            p.join(scratchRoot, 'authors', 'acme-labs.json'),
          ).readAsStringSync();
          return ProcessResult(1, 0, 'authors/ is sound\n', '');
        };
        expect(await enroll(registryDir: registry.path), 0, reason: '$err');
        expect(env.nodeCalls.first, ['--version']);
        expect(env.nodeCalls, hasLength(2));
        expect(authors, {'calvin.json', 'acme-labs.json'});
        expect(scripts, [
          'author-cert.mjs',
          'check-authors.mjs',
          p.join('lib', 'x.mjs'),
        ]);
        expect(newFile, written().readAsStringSync());
        expect(
          Directory(scratchRoot).existsSync(),
          isFalse,
          reason: 'the scratch copy is removed',
        );
        expect('$out', contains('authors/ is sound'));
        expect('$out', contains('checked with 1 other enrolled author'));
        expect('$out', isNot(contains('registry CI does that')));
        // The checkout itself is untouched.
        expect(
          File(p.join(registry.path, 'authors', 'acme-labs.json')).existsSync(),
          isFalse,
        );
      });

      test(
        'a refusal is exit 65 with the registry\'s text, and no file',
        () async {
          late String scratchRoot;
          env.node = (args) {
            scratchRoot = args[1];
            return ProcessResult(
              1,
              1,
              'authors/acme-labs.json: ed25519 key is already enrolled by '
                  'authors/calvin.json\n'
                  'authors/acme-labs.json: id prefix "com.calvinx.sub." overlaps '
                  '"com.calvinx." of authors/calvin.json\n',
              '',
            );
          };
          expect(await enroll(registryDir: registry.path), 65);
          expect('$err', contains('check-authors.mjs'));
          expect(
            '$err',
            contains('ed25519 key is already enrolled by authors/calvin.json'),
          );
          expect('$err', contains('overlaps "com.calvinx."'));
          expect('$err', contains('no file was written'));
          expect(written().existsSync(), isFalse);
          expect(work.listSync(), isEmpty);
          expect(Directory(scratchRoot).existsSync(), isFalse);
        },
      );

      test('an explicit --out is also left absent', () async {
        env.node = (_) => ProcessResult(1, 1, 'authors/x.json: bad\n', '');
        final target = p.join(work.path, 'elsewhere.json');
        expect(await enroll(registryDir: registry.path, outPath: target), 65);
        expect(File(target).existsSync(), isFalse);
      });

      test('a silent failure still reports the exit code', () async {
        env.node = (_) => ProcessResult(1, 3, '', '');
        expect(await enroll(registryDir: registry.path), 65);
        expect('$err', contains('exit code 3'));
      });

      test('exit 0 without the sound line is not trusted (exit 70)', () async {
        env.node = (_) => ProcessResult(1, 0, '', '');
        expect(await enroll(registryDir: registry.path), 70);
        expect('$err', contains('may not have run'));
        expect(work.listSync(), isEmpty);
      });

      test('node missing: warns and skips, the file is written', () async {
        env.nodeInstalled = false;
        expect(await enroll(registryDir: registry.path), 0, reason: '$err');
        expect('$err', contains('warning'));
        expect('$err', contains('node was not found'));
        expect('$out', contains('skipped'));
        expect(written().existsSync(), isTrue);
      });

      test(
        'a slug that is already enrolled is exit 65, before any work',
        () async {
          File(
            p.join(registry.path, 'authors', 'acme-labs.json'),
          ).writeAsStringSync('{}');
          expect(await enroll(registryDir: registry.path), 65);
          expect('$err', contains('already enrolled'));
          expect(env.openssl.calls, isEmpty);
          expect(env.nodeCalls, isEmpty);
          expect(written().existsSync(), isFalse);
        },
      );

      test('a directory that is not a registry is exit 66', () async {
        final empty = tempDir('hqplugin_enroll_notreg_');
        expect(await enroll(registryDir: empty.path), 66);
        expect('$err', contains('not a plugin-registry checkout'));
        expect(await enroll(registryDir: p.join(work.path, 'missing')), 66);
        expect(env.openssl.calls, isEmpty);
        expect(work.listSync(), isEmpty);
      });

      test('a registry without authors/ is fine', () async {
        Directory(p.join(registry.path, 'authors')).deleteSync(recursive: true);
        env.node = (_) => ProcessResult(1, 0, 'authors/ is sound\n', '');
        expect(await enroll(registryDir: registry.path), 0, reason: '$err');
        expect('$out', contains('checked with 0 other enrolled authors'));
      });
    });
  });
}

/// One runner for openssl, gh and node, each answering as a fake.
class _Env {
  final FakeOpenSsl openssl = FakeOpenSsl();

  /// GitHub login to numeric id, for `gh api users/<login> --jq .id`.
  final Map<String, String> ghUsers = {};

  /// `gh api user --jq .login`; null: it fails.
  String? ghLogin;
  bool ghInstalled = true;

  /// When set, every gh call fails with this on stderr.
  String? ghFailure;
  final List<List<String>> ghCalls = [];

  bool nodeInstalled = true;
  final List<List<String>> nodeCalls = [];

  /// Answers `node <script> <dir>`; the default says the registry is sound.
  ProcessResult Function(List<String> args) node = (_) =>
      ProcessResult(1, 0, 'authors/ is sound\n', '');

  Future<ProcessResult> call(String exe, List<String> args) async {
    switch (exe) {
      case 'gh':
        if (!ghInstalled) {
          throw ProcessException(exe, args, 'No such file or directory', 2);
        }
        ghCalls.add(args);
        if (ghFailure != null) return ProcessResult(1, 4, '', ghFailure);
        switch (args) {
          case ['api', final path, '--jq', '.id']
              when path.startsWith('users/'):
            final id = ghUsers[path.substring('users/'.length)];
            return id == null
                ? ProcessResult(1, 1, '', 'gh: Not Found (HTTP 404)\n')
                : ProcessResult(1, 0, '$id\n', '');
          case ['api', 'user', '--jq', '.login']:
            return ghLogin == null
                ? ProcessResult(1, 1, '', 'gh: not logged in\n')
                : ProcessResult(1, 0, '$ghLogin\n', '');
        }
        return ProcessResult(1, 2, '', 'fake gh: unexpected args $args\n');
      case 'node':
        if (!nodeInstalled) {
          throw ProcessException(exe, args, 'No such file or directory', 2);
        }
        nodeCalls.add(args);
        if (args.length == 1 && args.first == '--version') {
          return ProcessResult(1, 0, 'v22.0.0\n', '');
        }
        return node(args);
    }
    return openssl.call(exe, args);
  }
}
