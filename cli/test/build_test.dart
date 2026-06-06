import 'dart:io';

import 'package:hqplugin/src/build.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

Future<bool> _hasCargo() async {
  try {
    return (await Process.run('cargo', ['--version'])).exitCode == 0;
  } catch (_) {
    return false;
  }
}

void main() {
  group('runBuild — argument + precondition handling', () {
    test('missing --lang is a usage error', () async {
      final err = StringBuffer();
      final code = await runBuild(err_: err, out_: StringBuffer());
      expect(code, 64);
      expect(err.toString(), contains('--lang is required'));
    });

    test('unsupported language is reported, not crashed', () async {
      final err = StringBuffer();
      final code = await runBuild(lang: 'go', err_: err, out_: StringBuffer());
      expect(code, 2);
      expect(err.toString(), contains('not wired yet'));
    });

    test('rust build with no Cargo.toml fails cleanly', () async {
      final dir = Directory.systemTemp.createTempSync('hqplugin_nobuild_');
      addTearDown(() => dir.deleteSync(recursive: true));
      final err = StringBuffer();
      final code = await runBuild(
        lang: 'rust',
        entry: dir.path,
        err_: err,
        out_: StringBuffer(),
      );
      expect(code, 66);
      expect(err.toString(), contains('no Cargo.toml'));
    });
  });

  group('runBuild — real Rust build (needs cargo + wasm target)', () {
    test('builds the example crate to a .wasm', () async {
      if (!await _hasCargo()) {
        markTestSkipped('cargo not installed');
        return;
      }
      // The example crate lives at ../examples/portfolio_overview relative to
      // the cli package root (the test's CWD).
      final crate = p.normalize(
        p.join(Directory.current.path, '..', 'examples', 'portfolio_overview'),
      );
      if (!File(p.join(crate, 'Cargo.toml')).existsSync()) {
        markTestSkipped('example crate not found at $crate');
        return;
      }
      final outDir = Directory.systemTemp.createTempSync('hqplugin_out_');
      addTearDown(() => outDir.deleteSync(recursive: true));
      final out = p.join(outDir.path, 'plugin.wasm');

      final code = await runBuild(
        lang: 'rust',
        entry: crate,
        out: out,
        out_: StringBuffer(),
        err_: StringBuffer(),
      );

      if (code == 69) {
        markTestSkipped('cargo unavailable mid-run');
        return;
      }
      expect(code, 0, reason: 'build should succeed');
      final wasm = File(out);
      expect(wasm.existsSync(), isTrue);
      // Valid Wasm magic: \0asm.
      expect(wasm.readAsBytesSync().sublist(0, 4), [0x00, 0x61, 0x73, 0x6d]);
    }, timeout: const Timeout(Duration(minutes: 3)));
  });
}
