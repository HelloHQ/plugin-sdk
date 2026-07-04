import 'dart:io';

import 'package:path/path.dart' as p;

/// The Tier-2 Wasm target the host loads for Rust (no WASI imports).
const wasmTarget = 'wasm32-unknown-unknown';

/// Build world for the Go streaming-inference path (async `run`). Mirrors the
/// Rust SDK's `inference-quickstart` world.
const goInferenceWorld = 'hellohq-plugin-inference';

/// Build a plugin to a `.wasm` (or package a sidecar). Returns a process-style
/// exit code; diagnostics go to [out]/[err] (default stdout/stderr, injectable
/// for tests).
///
/// [inference] selects the streaming-inference path. It changes only the Go
/// build (Rust/JS select the `*-inference` world in-source, so their build
/// command is unchanged); for Go it requires the wasi-on-idle Go fork + the
/// preview1 reactor adapter (see [goBin]/[adapter]/[wit]), because the default
/// TinyGo/`wit-bindgen-go` path cannot drain `inference.complete`'s stream.
Future<int> runBuild({
  String? lang,
  String? entry,
  String out = 'plugin.wasm',
  bool inference = false,
  String? wit,
  String? adapter,
  String? goBin,
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
      // The async `inference-quickstart` world is selected by the crate's
      // `wit_bindgen::generate!`, so the build command is identical; --inference
      // is a no-op here beyond documentation.
      return _buildRust(entry ?? '.', out, o, e);
    case 'go':
      return inference
          ? _buildGoInference(entry ?? '.', out, wit, adapter, goBin, o, e)
          : _buildGo(entry ?? '.', out, o, e);
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
    e.writeln(
        'build: no go.mod or .go files in "$pkgDir". Pass --entry <pkg-dir>.');
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

/// Build a Go **streaming-inference** plugin into a Component.
///
/// This path differs from [_buildGo]: draining `inference.complete`'s
/// `stream<string>` needs the `bytecodealliance/wit-bindgen` Go backend
/// (readable `StreamReader[string]`, used to generate the committed bindings)
/// plus, at build time, the **wasi-on-idle Go fork** (so a blocked goroutine
/// yields to the async executor) and the **preview1 reactor adapter** (to
/// componentize the `wasip1` core). Those are currently unreleased, so they are
/// resolved explicitly:
///
///   - Go fork: [goBin] / `--go` or `$HQ_GO_WASI_ON_IDLE` (required — plain `go`
///     compiles but the component traps at runtime in the stream wait).
///   - WIT dir: [wit] / `--wit` or `$HQ_PLUGIN_WIT` (must define the
///     `hellohq-plugin-inference` world; the SDK's `sdks/go/wit` does).
///   - Adapter: [adapter] / `--adapter` or `$HQ_WASI_ADAPTER`
///     (wasi_snapshot_preview1.reactor.wasm).
///
/// See examples/component-quickstart-go/inference for the equivalent build.sh.
Future<int> _buildGoInference(
  String pkgDir,
  String out,
  String? wit,
  String? adapter,
  String? goBin,
  StringSink o,
  StringSink e,
) async {
  final env = Platform.environment;

  final hasGoFiles = Directory(pkgDir).existsSync() &&
      Directory(pkgDir)
          .listSync()
          .whereType<File>()
          .any((f) => f.path.endsWith('.go'));
  if (!File(p.join(pkgDir, 'go.mod')).existsSync() && !hasGoFiles) {
    e.writeln(
        'build: no go.mod or .go files in "$pkgDir". Pass --entry <pkg-dir>.');
    return 66; // EX_NOINPUT
  }

  final go = goBin ?? env['HQ_GO_WASI_ON_IDLE'];
  if (go == null) {
    e.writeln(
      'build --inference (go): the wasi-on-idle Go fork is required. Set '
      '--go <path> or \$HQ_GO_WASI_ON_IDLE to a go1.x-wasi-on-idle build '
      '(github.com/dicej/go releases). Plain `go` compiles but the component '
      'traps at runtime when draining the stream.',
    );
    return 69; // EX_UNAVAILABLE
  }
  final witDir = wit ?? env['HQ_PLUGIN_WIT'];
  if (witDir == null) {
    e.writeln(
      'build --inference (go): a WIT dir is required. Set --wit <dir> or '
      '\$HQ_PLUGIN_WIT to a directory defining the `$goInferenceWorld` world '
      '(e.g. the SDK\'s sdks/go/wit).',
    );
    return 64; // EX_USAGE
  }
  final adapterPath = adapter ?? env['HQ_WASI_ADAPTER'];
  if (adapterPath == null) {
    e.writeln(
      'build --inference (go): the preview1 reactor adapter is required. Set '
      '--adapter <path> or \$HQ_WASI_ADAPTER to wasi_snapshot_preview1.reactor'
      '.wasm (bytecodealliance/wasmtime releases).',
    );
    return 64; // EX_USAGE
  }
  if (!File(adapterPath).existsSync()) {
    e.writeln('build: adapter not found at "$adapterPath".');
    return 66;
  }
  if (!Directory(witDir).existsSync()) {
    e.writeln('build: --wit dir not found at "$witDir".');
    return 66;
  }
  if (!await _hasExecutable('wasm-tools')) {
    e.writeln(
      'build: wasm-tools not found — needed to embed + componentize. '
      'Install: cargo install wasm-tools',
    );
    return 69;
  }

  final outFile = File(out);
  if (outFile.parent.path.isNotEmpty) {
    outFile.parent.createSync(recursive: true);
  }
  final tmp = Directory.systemTemp.createTempSync('hqplugin_go_inf_');
  try {
    final core = p.join(tmp.path, 'core.wasm');
    final withWit = p.join(tmp.path, 'with-wit.wasm');

    // 1. Compile the wasip1 core module with the Go fork (c-shared reactor).
    o.writeln('build: GOOS=wasip1 GOARCH=wasm $go build (c-shared)  ($pkgDir)');
    final build = await Process.run(
      go,
      [
        'build',
        '-o',
        core,
        '-buildmode=c-shared',
        '-ldflags=-checklinkname=0',
        '.',
      ],
      workingDirectory: pkgDir,
      environment: {...env, 'GOOS': 'wasip1', 'GOARCH': 'wasm'},
    );
    if ('${build.stdout}'.isNotEmpty) o.write(build.stdout);
    if ('${build.stderr}'.isNotEmpty) e.write(build.stderr);
    if (build.exitCode != 0) return build.exitCode;

    // 2. Embed the WIT against the inference world.
    o.writeln('build: wasm-tools component embed (world: $goInferenceWorld)');
    final embed = await Process.run('wasm-tools', [
      'component',
      'embed',
      witDir,
      '--world',
      goInferenceWorld,
      core,
      '--output',
      withWit,
    ]);
    if ('${embed.stdout}'.isNotEmpty) o.write(embed.stdout);
    if ('${embed.stderr}'.isNotEmpty) e.write(embed.stderr);
    if (embed.exitCode != 0) return 70; // EX_SOFTWARE

    // 3. Adapt the wasip1 module into a Component.
    o.writeln('build: wasm-tools component new --adapt <preview1-reactor>');
    final comp = await Process.run('wasm-tools', [
      'component',
      'new',
      '--adapt',
      adapterPath,
      withWit,
      '--output',
      outFile.absolute.path,
    ]);
    if ('${comp.stdout}'.isNotEmpty) o.write(comp.stdout);
    if ('${comp.stderr}'.isNotEmpty) e.write(comp.stderr);
    if (comp.exitCode != 0) return 70;

    if (!outFile.existsSync()) {
      e.writeln('build: componentize reported success but $out was not produced.');
      return 70;
    }
    o.writeln('build: ✓ → $out (component, ${outFile.lengthSync()} bytes)');
    return 0;
  } finally {
    tmp.deleteSync(recursive: true);
  }
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

  // Package the core module into a WebAssembly **Component** (the
  // `hellohq:plugin@0.1.0` ABI the host loads). `wasm-tools component new`
  // reads the `component-type` custom section wit-bindgen embeds and
  // tree-shakes unused capability imports out. No WASI adapter is needed —
  // the SDK's `setup_guest!` keeps the import surface to `hellohq:plugin/*`.
  final comp = await _componentize(out, o, e);
  if (comp != 0) return comp;

  o.writeln('build: ✓ ${p.basename(src.path)} → $out '
      '(component, ${outFile.lengthSync()} bytes)');
  return 0;
}

/// Package a core `.wasm` module into a Component, in place at [wasmPath].
Future<int> _componentize(String wasmPath, StringSink o, StringSink e) async {
  if (!await _hasExecutable('wasm-tools')) {
    e.writeln(
      'build: wasm-tools not found — needed to package the Component. '
      'Install: cargo install wasm-tools',
    );
    return 69; // EX_UNAVAILABLE
  }
  final tmp = '$wasmPath.component';
  final r = await Process.run('wasm-tools', [
    'component',
    'new',
    wasmPath,
    '-o',
    tmp,
  ]);
  if (r.exitCode != 0) {
    if ('${r.stdout}'.isNotEmpty) o.write(r.stdout);
    if ('${r.stderr}'.isNotEmpty) e.write(r.stderr);
    e.writeln(
      'build: `wasm-tools component new` failed. Is the crate built with '
      'hellohq-plugin-sdk (so it embeds the component-type section)?',
    );
    return 70; // EX_SOFTWARE
  }
  File(tmp).renameSync(wasmPath);
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
