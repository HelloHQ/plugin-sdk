// Opt-in: dry-runs `hqplugin publish` against the REAL released hello-world
// (https://github.com/HelloHQ/plugin-sdk/releases/tag/hello-world-v1.0.0).
// It downloads the released plugin.wasm and icon.svg over the network, so it
// is skipped unless HQPLUGIN_NETWORK_TESTS=1:
//
//   HQPLUGIN_NETWORK_TESTS=1 dart test test/publish_network_test.dart
//
// Only the HTTP side is real. gh/git/node go through a fake runner that
// answers the --first-party membership check and fails the test on anything
// that would change state, so the test needs no gh login and mutates nothing.
import 'dart:convert';
import 'dart:io';

import 'package:hqplugin/src/publish.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

const _releasedHash =
    '6f2e89607eb6642de832b5ad653408ee00840ad89b26d8cce57aab6af4c22706';
const _release =
    'https://github.com/HelloHQ/plugin-sdk/releases/download/hello-world-v1.0.0';

void main() {
  final enabled = Platform.environment['HQPLUGIN_NETWORK_TESTS'] == '1';

  test(
    'dry run pins the released hello-world plugin.wasm and icon.svg hashes',
    () async {
      // The example manifest, as committed (placeholder hash), from the repo,
      // pointed at the released v1.0.0 assets. The version is far above
      // anything the registry will list, so this stays a plan for an update
      // whatever the registry holds (the version does not affect the pins).
      final exampleDir = p.join(
        Directory.current.path,
        '..',
        'examples',
        'hello-world',
      );
      final manifest =
          jsonDecode(
                File(p.join(exampleDir, 'manifest.json')).readAsStringSync(),
              )
              as Map<String, dynamic>;
      expect(manifest['content_hash_sha256'], '0' * 64);
      expect(manifest.containsKey('sidebar_icon_hash_sha256'), isFalse);
      manifest
        ..['version'] = '1000.0.0'
        ..['wasm_url'] = '$_release/plugin.wasm'
        ..['sidebar_icon'] = '$_release/icon.svg';
      // The released icon is the committed one.
      final iconHash = sha256Hex(
        File(p.join(exampleDir, 'icon.svg')).readAsBytesSync(),
      );

      final dir = Directory.systemTemp.createTempSync('hqplugin_network_');
      addTearDown(() => dir.deleteSync(recursive: true));
      File(
        p.join(dir.path, 'manifest.json'),
      ).writeAsStringSync(jsonEncode(manifest));

      final calls = <String>[];
      Future<ProcessResult> runner(
        String exe,
        List<String> args, {
        String? workingDirectory,
      }) async {
        final line = '$exe ${args.join(' ')}';
        calls.add(line);
        return switch (args) {
          ['--version'] => ProcessResult(0, 0, 'gh version\n', ''),
          ['api', 'user', '--jq', '.login'] => ProcessResult(
            0,
            0,
            'hellohq-maintainer\n',
            '',
          ),
          ['api', final path, '--silent']
              when path.startsWith('orgs/HelloHQ/members/') =>
            ProcessResult(0, 0, '', ''),
          _ => throw StateError('unexpected command in a dry run: $line'),
        };
      }

      final hashes = <String, String>{};
      Future<HttpHop> get(Uri url, {required int maxBytes}) async {
        final hop = await defaultHttpGet(url, maxBytes: maxBytes);
        if (hop.statusCode == 200) hashes['$url'] = sha256Hex(hop.body);
        return hop;
      }

      final out = StringBuffer();
      final err = StringBuffer();
      final code = await runPublish(
        dryRun: true,
        firstParty: true, // hello-world is provenance core, trust_tier official
        out_: out,
        err_: err,
        commandRunner: runner,
        httpGet: get,
        workingDirectory: dir.path,
      );

      expect(code, 0, reason: 'out:\n$out\nerr:\n$err');
      // The plan pins the hashes of the bytes GitHub served.
      // (Keyed by the final hop: GitHub redirects release assets to its CDN.)
      expect(hashes.values, containsAll([_releasedHash, iconHash]));
      final plan = out.toString();
      expect(plan, contains('"content_hash_sha256": "$_releasedHash"'));
      expect(plan, contains('"sidebar_icon_hash_sha256": "$iconHash"'));
      expect(plan, contains('plan only'));
      expect(calls.where((c) => !c.endsWith('--version')), [
        'gh api user --jq .login',
        'gh api orgs/HelloHQ/members/hellohq-maintainer --silent',
      ]);
      // ignore: avoid_print
      print(out);
    },
    skip: enabled ? false : 'set HQPLUGIN_NETWORK_TESTS=1 to run',
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
