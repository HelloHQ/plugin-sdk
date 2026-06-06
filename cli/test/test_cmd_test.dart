import 'dart:io';

import 'package:hqplugin/src/build.dart';
import 'package:hqplugin/src/test_cmd.dart';
import 'package:hqplugin/src/wasm/wasm_runner.dart';
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
  group('runTest — guards', () {
    test('missing --wasm is a usage error', () async {
      final err = StringBuffer();
      expect(await runTest(err_: err, out_: StringBuffer()), 64);
      expect(err.toString(), contains('--wasm is required'));
    });

    test('missing file fails cleanly', () async {
      final err = StringBuffer();
      final code = await runTest(
        wasmPath: '/no/such/plugin.wasm',
        err_: err,
        out_: StringBuffer(),
      );
      expect(code, 66);
      expect(err.toString(), contains('no such file'));
    });
  });

  group('runTest — full build → run loop (needs cargo + wasm runtime)', () {
    test('builds the SDK example and runs it against the mock host', () async {
      if (!await _hasCargo()) {
        markTestSkipped('cargo not installed');
        return;
      }
      if (!WasmRunner.isRuntimeAvailable) {
        markTestSkipped('wasmtime not provisioned (scripts/fetch-wasmtime-libs.sh)');
        return;
      }
      final crate = p.normalize(
        p.join(Directory.current.path, '..', 'examples', 'portfolio_overview'),
      );
      if (!File(p.join(crate, 'Cargo.toml')).existsSync()) {
        markTestSkipped('example crate missing');
        return;
      }

      final tmp = Directory.systemTemp.createTempSync('hqp_loop_');
      addTearDown(() => tmp.deleteSync(recursive: true));
      final wasm = p.join(tmp.path, 'plugin.wasm');

      // 1. build
      final buildCode = await runBuild(
        lang: 'rust',
        entry: crate,
        out: wasm,
        out_: StringBuffer(),
        err_: StringBuffer(),
      );
      expect(buildCode, 0, reason: 'build should succeed');

      // 2. test — granted → renders the demo portfolios
      final out = StringBuffer();
      final code = await runTest(
        wasmPath: wasm,
        grants: const ['read:portfolio_names'],
        out_: out,
        err_: StringBuffer(),
      );
      expect(code, 0);
      expect(out.toString(), contains('key-value-list'));
      expect(out.toString(), contains('Personal'));

      // 3. test — denied → graceful empty-state, still exit 0
      final out2 = StringBuffer();
      final code2 =
          await runTest(wasmPath: wasm, out_: out2, err_: StringBuffer());
      expect(code2, 0);
      expect(out2.toString(), contains('empty-state'));
    }, timeout: const Timeout(Duration(minutes: 3)));
  });
}
