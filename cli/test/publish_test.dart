import 'dart:convert';
import 'dart:io';

import 'package:hqplugin/src/publish.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  test('submit pushes to the authenticated user fork and opens an upstream PR',
      () async {
    final dir = Directory.systemTemp.createTempSync('hqplugin_publish_');
    // Windows holds a directory handle briefly after Directory.current is
    // restored, preventing immediate deletion. Retry with a short delay.
    addTearDown(() async {
      for (var attempt = 0; attempt < 12; attempt++) {
        try {
          dir.deleteSync(recursive: true);
          return;
        } catch (_) {
          await Future.delayed(const Duration(milliseconds: 250));
        }
      }
      // Best-effort: on Windows a CWD handle can linger several seconds.
      // Temp dirs are cleaned by the OS on restart; don't fail the test.
      if (Platform.isWindows) {
        try { dir.deleteSync(recursive: true); } catch (_) {}
      } else {
        dir.deleteSync(recursive: true); // final attempt — let it throw
      }
    });

    File(p.join(dir.path, 'manifest.json')).writeAsStringSync(jsonEncode({
      'id': 'com.example.summary',
      'name': 'Summary',
      'version': '0.0.0',
    }));
    File(p.join(dir.path, 'plugin.wasm')).writeAsBytesSync([0, 97, 115, 109]);

    final calls = <String>[];
    Future<ProcessResult> runner(
      String executable,
      List<String> arguments, {
      String? workingDirectory,
    }) async {
      calls.add('$executable ${arguments.join(' ')}');
      if (executable == 'shasum') {
        return ProcessResult(
          1,
          0,
          '${List.filled(64, 'a').join()}  ${p.join(dir.path, 'plugin.wasm')}\n',
          '',
        );
      }
      if (arguments case ['api', 'user', '--jq', '.login']) {
        return ProcessResult(1, 0, 'octocat\n', '');
      }
      if (arguments
          case [
            'repo',
            'view',
            'octocat/plugin-registry',
            '--json',
            'parent',
            '--jq',
            '.parent.nameWithOwner'
          ]) {
        return ProcessResult(1, 1, '', 'not found');
      }
      if (arguments
          case ['repo', 'clone', 'octocat/plugin-registry', final clonePath]) {
        Directory(clonePath).createSync(recursive: true);
      }
      if (arguments case ['pr', 'create', ...]) {
        return ProcessResult(
            1, 0, 'https://github.com/HelloHQ/plugin-registry/pull/1\n', '');
      }
      return ProcessResult(1, 0, '', '');
    }

    final previous = Directory.current;
    Directory.current = dir;
    addTearDown(() => Directory.current = previous);

    final out = StringBuffer();
    final err = StringBuffer();
    final code = await runPublish(
      version: '1.2.3',
      submit: true,
      out_: out,
      err_: err,
      commandRunner: runner,
    );

    expect(code, 0, reason: err.toString());
    expect(
      calls,
      contains('gh repo fork HelloHQ/plugin-registry --clone=false'),
    );
    expect(
      calls.any(
        (call) => call.startsWith(
          'gh repo clone octocat/plugin-registry ',
        ),
      ),
      isTrue,
    );
    expect(calls, contains('git fetch upstream main'));
    expect(
      calls,
      contains('git push -u origin plugin/com.example.summary-1.2.3'),
    );
    expect(
      calls.singleWhere((call) => call.startsWith('gh pr create ')),
      contains('--head octocat:plugin/com.example.summary-1.2.3 --base main'),
    );
    expect(out.toString(), contains('PR opened'));
  });

  test('submit stops when git commit fails', () async {
    final dir = Directory.systemTemp.createTempSync('hqplugin_publish_');
    addTearDown(() async {
      for (var attempt = 0; attempt < 12; attempt++) {
        try {
          dir.deleteSync(recursive: true);
          return;
        } catch (_) {
          await Future.delayed(const Duration(milliseconds: 250));
        }
      }
      if (Platform.isWindows) {
        try { dir.deleteSync(recursive: true); } catch (_) {}
      } else {
        dir.deleteSync(recursive: true);
      }
    });

    File(p.join(dir.path, 'manifest.json')).writeAsStringSync(jsonEncode({
      'id': 'com.example.summary',
    }));
    File(p.join(dir.path, 'plugin.wasm')).writeAsBytesSync([0, 97, 115, 109]);

    final calls = <String>[];
    Future<ProcessResult> runner(
      String executable,
      List<String> arguments, {
      String? workingDirectory,
    }) async {
      calls.add('$executable ${arguments.join(' ')}');
      if (executable == 'shasum') {
        return ProcessResult(
          1,
          0,
          '${List.filled(64, 'b').join()}  plugin.wasm\n',
          '',
        );
      }
      if (arguments case ['api', 'user', '--jq', '.login']) {
        return ProcessResult(1, 0, 'octocat\n', '');
      }
      if (arguments case [
        'repo',
        'view',
        'octocat/plugin-registry',
        '--json',
        'parent',
        '--jq',
        '.parent.nameWithOwner'
      ]) {
        return ProcessResult(1, 0, 'HelloHQ/plugin-registry\n', '');
      }
      if (arguments
          case ['repo', 'clone', 'octocat/plugin-registry', final clonePath]) {
        Directory(clonePath).createSync(recursive: true);
      }
      if (executable == 'git' && arguments.first == 'commit') {
        return ProcessResult(1, 128, '', 'commit failed\n');
      }
      return ProcessResult(1, 0, '', '');
    }

    final previous = Directory.current;
    Directory.current = dir;
    addTearDown(() => Directory.current = previous);

    final err = StringBuffer();
    final code = await runPublish(
      version: '1.2.3',
      submit: true,
      out_: StringBuffer(),
      err_: err,
      commandRunner: runner,
    );

    expect(code, 128);
    expect(err.toString(), contains('git commit failed'));
    expect(calls.any((call) => call.startsWith('git push ')), isFalse);
    expect(calls.any((call) => call.startsWith('gh pr create ')), isFalse);
  });

  // ── Provenance + licensing gates ──────────────────────────────────────────
  // These reject before any artifact hashing or network call, so the command
  // runner must never be invoked.

  group('provenance/licensing gates', () {
    /// Writes [manifest] to a temp cwd and runs publish with a runner that
    /// fails the test if it is ever called. Returns (exitCode, stderr).
    Future<({int code, String err})> runWith(
      Map<String, dynamic> manifest,
    ) async {
      final dir = Directory.systemTemp.createTempSync('hqplugin_gate_');
      addTearDown(() {
        try {
          dir.deleteSync(recursive: true);
        } catch (_) {}
      });
      File(p.join(dir.path, 'manifest.json'))
          .writeAsStringSync(jsonEncode(manifest));

      final calls = <String>[];
      Future<ProcessResult> runner(
        String executable,
        List<String> arguments, {
        String? workingDirectory,
      }) async {
        calls.add(executable);
        return ProcessResult(1, 0, '', '');
      }

      final previous = Directory.current;
      Directory.current = dir;
      addTearDown(() => Directory.current = previous);

      final err = StringBuffer();
      final code = await runPublish(
        version: '1.0.0',
        submit: true,
        out_: StringBuffer(),
        err_: err,
        commandRunner: runner,
      );
      // No process should have run for a gated rejection.
      expect(calls, isEmpty, reason: 'gate must reject before any process call');
      return (code: code, err: err.toString());
    }

    test('rejects enterprise provenance', () async {
      final r = await runWith({
        'id': 'com.example.internal',
        'provenance': 'enterprise',
      });
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('enterprise'));
    });

    test('rejects core provenance', () async {
      final r = await runWith({
        'id': 'com.hellohq.thing',
        'provenance': 'core',
      });
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('core'));
    });

    test('rejects commercial licensing', () async {
      final r = await runWith({
        'id': 'com.example.pro',
        'licensing': {'kind': 'commercial', 'product_id': 'sku_x'},
      });
      expect(r.code, 65);
      expect(r.err.toLowerCase(), contains('commercial'));
    });

    test('warns (but does not fail) on open-source without spdx', () async {
      // open_source + no spdx is a warning; the publish still proceeds past the
      // gate to hashing, where the missing plugin.wasm makes it exit 66.
      final r = await runWith({
        'id': 'com.example.oss',
        'licensing': {'kind': 'open_source'},
      });
      expect(r.code, 66); // reached the wasm-not-found check → not gated out
      expect(r.err.toLowerCase(), contains('spdx'));
    });
  });
}
