import 'dart:io';

import 'package:path/path.dart' as p;

/// The Tier-2 Wasm target the host loads for Rust (no WASI imports).
const wasmTarget = 'wasm32-unknown-unknown';

/// Build a plugin to a `.wasm` (or package a sidecar). Returns a process-style
/// exit code; diagnostics go to [out]/[err] (default stdout/stderr, injectable
/// for tests).
Future<int> runBuild({
  String? lang,
  String? entry,
  String out = 'plugin.wasm',
  StringSink? out_,
  StringSink? err_,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;

  if (lang == null) {
    e.writeln('build: --lang is required (rust|go|typescript|python).');
    return 64; // EX_USAGE
  }
  switch (lang) {
    case 'rust':
      return _buildRust(entry ?? '.', out, o, e);
    case 'go':
      return _buildGo(entry ?? '.', out, o, e);
    case 'typescript':
    case 'python':
      e.writeln(
        'build --lang $lang is not wired yet. TypeScript (WebView ui.zip '
        'bundling) and Python (sidecar bundle) need a defined package format; '
        'use the SDK build steps in sdks/$lang for now. Rust and Go are '
        'supported.',
      );
      return 2;
    default:
      e.writeln('build: unknown --lang "$lang".');
      return 64;
  }
}

/// Build a Go plugin to a WASI-preview1 `.wasm` (the host's Go execution mode).
Future<int> _buildGo(
  String pkgDir,
  String out,
  StringSink o,
  StringSink e,
) async {
  final goMod = p.join(pkgDir, 'go.mod');
  final hasGoFiles = Directory(pkgDir).existsSync() &&
      Directory(pkgDir)
          .listSync()
          .whereType<File>()
          .any((f) => f.path.endsWith('.go'));
  if (!File(goMod).existsSync() && !hasGoFiles) {
    e.writeln('build: no go.mod or .go files in "$pkgDir". Pass --entry <pkg-dir>.');
    return 66; // EX_NOINPUT
  }
  if (!await _hasGoToolchain()) {
    e.writeln('build: go not found. Install Go from https://go.dev/dl');
    return 69; // EX_UNAVAILABLE
  }

  final outFile = File(out);
  if (outFile.parent.path.isNotEmpty) {
    outFile.parent.createSync(recursive: true);
  }
  // Absolute output path so `go build` (run with workingDirectory) targets it.
  final outAbs = outFile.absolute.path;

  o.writeln('build: GOOS=wasip1 GOARCH=wasm go build -o $out  ($pkgDir)');
  final result = await Process.run(
    'go',
    ['build', '-o', outAbs, '.'],
    workingDirectory: pkgDir,
    environment: {
      ...Platform.environment,
      'GOOS': 'wasip1',
      'GOARCH': 'wasm',
    },
  );
  if ('${result.stdout}'.isNotEmpty) o.write(result.stdout);
  if ('${result.stderr}'.isNotEmpty) e.write(result.stderr);
  if (result.exitCode != 0) return result.exitCode;

  if (!outFile.existsSync()) {
    e.writeln('build: go build reported success but $out was not produced.');
    return 70; // EX_SOFTWARE
  }
  o.writeln('build: ✓ → $out (${outFile.lengthSync()} bytes)');
  return 0;
}

Future<int> _buildRust(
  String crateDir,
  String out,
  StringSink o,
  StringSink e,
) async {
  final manifest = p.join(crateDir, 'Cargo.toml');
  if (!File(manifest).existsSync()) {
    e.writeln('build: no Cargo.toml in "$crateDir". Pass --entry <crate-dir>.');
    return 66; // EX_NOINPUT
  }
  if (!await _hasExecutable('cargo')) {
    e.writeln('build: cargo not found. Install Rust from https://rustup.rs');
    return 69; // EX_UNAVAILABLE
  }

  o.writeln('build: cargo build --target $wasmTarget --release  ($crateDir)');
  final result = await Process.run(
    'cargo',
    ['build', '--target', wasmTarget, '--release'],
    workingDirectory: crateDir,
  );
  if ('${result.stdout}'.isNotEmpty) o.write(result.stdout);
  if ('${result.stderr}'.isNotEmpty) e.write(result.stderr);

  if (result.exitCode != 0) {
    if ('${result.stderr}'.contains(wasmTarget) ||
        '${result.stderr}'.contains('target may not be installed')) {
      e.writeln('\nHint: rustup target add $wasmTarget');
    }
    return result.exitCode;
  }

  final releaseDir = Directory(
    p.join(crateDir, 'target', wasmTarget, 'release'),
  );
  final wasms = releaseDir.existsSync()
      ? releaseDir
            .listSync()
            .whereType<File>()
            .where((f) => f.path.endsWith('.wasm'))
            .toList()
      : <File>[];
  if (wasms.isEmpty) {
    e.writeln(
      'build: no .wasm produced under ${releaseDir.path}. '
      'Is the crate `crate-type = ["cdylib"]`? '
      '(Workspaces share a target dir — pass the leaf crate as --entry.)',
    );
    return 70; // EX_SOFTWARE
  }
  // Prefer the freshest artifact when several exist.
  wasms.sort(
    (a, b) => b.statSync().modified.compareTo(a.statSync().modified),
  );
  final src = wasms.first;

  final outFile = File(out);
  if (outFile.parent.path.isNotEmpty) {
    outFile.parent.createSync(recursive: true);
  }
  src.copySync(out);
  o.writeln('build: ✓ ${p.basename(src.path)} → $out '
      '(${outFile.lengthSync()} bytes)');
  return 0;
}

Future<bool> _hasExecutable(String name) async {
  try {
    final r = await Process.run(name, ['--version']);
    return r.exitCode == 0;
  } catch (_) {
    return false;
  }
}

/// Go uses `go version` (not `--version`), so it needs its own probe.
Future<bool> _hasGoToolchain() async {
  try {
    return (await Process.run('go', ['version'])).exitCode == 0;
  } catch (_) {
    return false;
  }
}
