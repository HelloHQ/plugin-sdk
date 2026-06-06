import 'dart:ffi';
import 'dart:io';

import 'package:hqplugin/src/wasm/wasmtime_bindings.dart';
import 'package:path/path.dart' as p;

/// Resolves and opens the vendored `libwasmtime` for the CLI's Wasm runner.
///
/// The library is provisioned into `<plugin-sdk>/third_party/wasmtime/<plat>/`
/// by `scripts/fetch-wasmtime-libs.sh`. Because `hqplugin` runs from an author's
/// plugin directory (arbitrary CWD), the loader searches **upward** from both
/// the CWD and the running script/executable directory for that tree, and also
/// honours `$HQPLUGIN_WASMTIME_LIB`.
class CliWasmtimeLib {
  CliWasmtimeLib._();

  static WasmtimeBindings? _bindings;
  static DynamicLibrary? _lib;

  static WasmtimeBindings get bindings =>
      _bindings ??= WasmtimeBindings(_open());

  static bool get isAvailable {
    try {
      _open();
      return true;
    } catch (_) {
      return false;
    }
  }

  static DynamicLibrary _open() {
    if (_lib != null) return _lib!;
    final path = _resolve();
    if (path == null) {
      throw StateError(
        'libwasmtime not found. Run scripts/fetch-wasmtime-libs.sh in the '
        'plugin-sdk repo, or set HQPLUGIN_WASMTIME_LIB to the library path.',
      );
    }
    return _lib = DynamicLibrary.open(path);
  }

  static String? _resolve() {
    final env = Platform.environment['HQPLUGIN_WASMTIME_LIB'];
    if (env != null && File(env).existsSync()) return env;

    final fileName = _libFileName();
    final plat = _platformDir();
    if (fileName == null || plat == null) return null;
    final rel = p.join('third_party', 'wasmtime', plat, fileName);

    final roots = <String>{
      Directory.current.path,
      p.dirname(Platform.script.toFilePath(windows: Platform.isWindows)),
      p.dirname(Platform.resolvedExecutable),
    };
    for (final root in roots) {
      final hit = _searchUp(root, rel);
      if (hit != null) return hit;
    }
    return null;
  }

  /// Walk up from [start], checking `<dir>/<rel>` at each level.
  static String? _searchUp(String start, String rel) {
    var dir = Directory(start).absolute.path;
    for (var i = 0; i < 8; i++) {
      final candidate = p.join(dir, rel);
      if (File(candidate).existsSync()) return candidate;
      final parent = p.dirname(dir);
      if (parent == dir) break;
      dir = parent;
    }
    return null;
  }

  static String? _libFileName() {
    if (Platform.isMacOS) return 'libwasmtime.dylib';
    if (Platform.isLinux) return 'libwasmtime.so';
    if (Platform.isWindows) return 'wasmtime.dll';
    return null;
  }

  static String? _platformDir() {
    final abi = Abi.current();
    if (Platform.isMacOS) {
      return abi == Abi.macosArm64 ? 'macos-arm64' : 'macos-x64';
    }
    if (Platform.isLinux) {
      return abi == Abi.linuxArm64 ? 'linux-arm64' : 'linux-x64';
    }
    if (Platform.isWindows) return 'windows-x64';
    return null;
  }
}
