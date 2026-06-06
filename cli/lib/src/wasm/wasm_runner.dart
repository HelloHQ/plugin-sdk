import 'dart:convert';
import 'dart:ffi';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';
import 'package:hqplugin/src/wasm/wasmtime_bindings.dart';
import 'package:hqplugin/src/wasm/wasmtime_loader.dart';

/// Resolver for the `env.hq_read` host import: request JSON → response JSON.
typedef HostResolver = String Function(String requestJson);

/// Sink for `env.emit_event`: (name, payload JSON).
typedef EventSink = void Function(String name, String payload);

/// Thrown on a Wasm compile / link / instantiate / run failure.
class WasmRunError implements Exception {
  final String message;
  const WasmRunError(this.message);
  @override
  String toString() => 'WasmRunError: $message';
}

/// Minimal in-CLI Wasm runner — a focused port of the host's
/// `PluginWasmService` over the same Wasmtime C-API bindings. Loads a plugin
/// `.wasm`, wires `env.hq_read` (+ `env.emit_event`), and runs `run(ptr,len)`.
///
/// Fuel-metered so a runaway plugin traps instead of hanging the CLI.
class WasmRunner {
  final HostResolver resolver;
  final EventSink? onEvent;
  final int fuelLimit;

  WasmRunner({required this.resolver, this.onEvent, this.fuelLimit = 1000000000});

  WasmtimeBindings get _b => CliWasmtimeLib.bindings;

  Pointer<wasm_engine_t> _engine = nullptr;
  Pointer<wasmtime_store_t> _store = nullptr;
  Pointer<wasmtime_context_t> _context = nullptr;
  Pointer<wasmtime_module_t> _module = nullptr;
  Pointer<wasmtime_linker_t> _linker = nullptr;
  Pointer<wasmtime_instance_t> _instance = nullptr;
  Pointer<wasmtime_memory_t> _memory = nullptr;
  final List<NativeCallable> _callbacks = [];
  bool _loaded = false;

  /// Whether the native Wasmtime library is available.
  static bool get isRuntimeAvailable => CliWasmtimeLib.isAvailable;

  void load(Uint8List wasm) {
    if (_loaded) return;

    final config = _b.wasm_config_new();
    _b.wasmtime_config_consume_fuel_set(config, true);
    _engine = _b.wasm_engine_new_with_config(config);
    if (_engine == nullptr) throw const WasmRunError('engine_new failed');
    _store = _b.wasmtime_store_new(_engine, nullptr, nullptr);
    _context = _b.wasmtime_store_context(_store);
    _applyFuel();

    final buf = calloc<Uint8>(wasm.length);
    buf.asTypedList(wasm.length).setAll(0, wasm);
    final modOut = calloc<Pointer<wasmtime_module_t>>();
    try {
      _check(_b.wasmtime_module_new(_engine, buf, wasm.length, modOut),
          'module_new');
      _module = modOut.value;
    } finally {
      calloc.free(buf);
      calloc.free(modOut);
    }

    _linker = _b.wasmtime_linker_new(_engine);
    _defineHqRead();
    _defineEmitEvent();

    _instance = calloc<wasmtime_instance_t>();
    final trap = calloc<Pointer<wasm_trap_t>>();
    try {
      _check(
        _b.wasmtime_linker_instantiate(_linker, _context, _module, _instance, trap),
        'instantiate',
        trap: trap.value,
      );
    } finally {
      calloc.free(trap);
    }
    _loaded = true;
  }

  /// Run `run(in_ptr, in_len) -> i64`, returning the decoded output JSON.
  String runBytes(String input) {
    if (!_loaded) throw const WasmRunError('not loaded');
    _applyFuel();
    final inBytes = Uint8List.fromList(utf8.encode(input));
    final inPtr = _callInt('alloc', [inBytes.length]);
    _writeMemory(inPtr, inBytes);
    final packed = _callInt('run', [inPtr, inBytes.length]);
    final outPtr = (packed >> 32) & 0xFFFFFFFF;
    final outLen = packed & 0xFFFFFFFF;
    return utf8.decode(_readMemory(outPtr, outLen));
  }

  void dispose() {
    for (final cb in _callbacks) {
      cb.close();
    }
    _callbacks.clear();
    if (_memory != nullptr) calloc.free(_memory);
    if (_instance != nullptr) calloc.free(_instance);
    if (_linker != nullptr) _b.wasmtime_linker_delete(_linker);
    if (_module != nullptr) _b.wasmtime_module_delete(_module);
    if (_store != nullptr) _b.wasmtime_store_delete(_store);
    if (_engine != nullptr) _b.wasm_engine_delete(_engine);
    _memory = _instance = _linker = _module = _store = _engine = _context =
        nullptr;
    _loaded = false;
  }

  // ── Host imports ────────────────────────────────────────────────────────────

  void _defineHqRead() {
    final cb = NativeCallable<wasmtime_func_callback_tFunction>.isolateLocal((
      Pointer<Void> env,
      Pointer<wasmtime_caller_t> caller,
      Pointer<wasmtime_val_t> args,
      int nargs,
      Pointer<wasmtime_val_t> results,
      int nresults,
    ) {
      final reqPtr = args.ref.of.i32;
      final reqLen = (args + 1).ref.of.i32;
      final request = utf8.decode(_readMemory(reqPtr, reqLen));
      final response = utf8.encode(resolver(request));
      final respPtr = _callInt('alloc', [response.length]);
      _writeMemory(respPtr, Uint8List.fromList(response));
      final packed = (respPtr << 32) | response.length;
      results.ref
        ..kind = WASMTIME_I64
        ..of.i64 = packed;
      return nullptr;
    });
    _callbacks.add(cb);
    _defineFunc('hq_read', const [WASMTIME_I32, WASMTIME_I32], WASMTIME_I64,
        cb.nativeFunction);
  }

  void _defineEmitEvent() {
    final cb = NativeCallable<wasmtime_func_callback_tFunction>.isolateLocal((
      Pointer<Void> env,
      Pointer<wasmtime_caller_t> caller,
      Pointer<wasmtime_val_t> args,
      int nargs,
      Pointer<wasmtime_val_t> results,
      int nresults,
    ) {
      final name = utf8.decode(_readMemory(args.ref.of.i32, (args + 1).ref.of.i32));
      final payload =
          utf8.decode(_readMemory((args + 2).ref.of.i32, (args + 3).ref.of.i32));
      onEvent?.call(name, payload);
      return nullptr;
    });
    _callbacks.add(cb);
    _defineFunc(
      'emit_event',
      const [WASMTIME_I32, WASMTIME_I32, WASMTIME_I32, WASMTIME_I32],
      null,
      cb.nativeFunction,
    );
  }

  void _defineFunc(
    String name,
    List<int> paramKinds,
    int? resultKind,
    wasmtime_func_callback_t nativeFn,
  ) {
    final params = calloc<Pointer<wasm_valtype_t>>(
      paramKinds.isEmpty ? 1 : paramKinds.length,
    );
    for (var i = 0; i < paramKinds.length; i++) {
      params[i] = _b.wasm_valtype_new(_valkind(paramKinds[i]));
    }
    final paramsVec = calloc<wasm_valtype_vec_t>();
    _b.wasm_valtype_vec_new(paramsVec, paramKinds.length, params);
    final resultsVec = calloc<wasm_valtype_vec_t>();
    if (resultKind != null) {
      final r = calloc<Pointer<wasm_valtype_t>>(1);
      r[0] = _b.wasm_valtype_new(_valkind(resultKind));
      _b.wasm_valtype_vec_new(resultsVec, 1, r);
      calloc.free(r);
    } else {
      _b.wasm_valtype_vec_new(resultsVec, 0, nullptr);
    }
    final funcType = _b.wasm_functype_new(paramsVec, resultsVec);
    final mod = 'env'.toNativeUtf8();
    final nm = name.toNativeUtf8();
    try {
      _check(
        _b.wasmtime_linker_define_func(
          _linker,
          mod.cast<Char>(),
          mod.length,
          nm.cast<Char>(),
          nm.length,
          funcType,
          nativeFn,
          nullptr,
          nullptr,
        ),
        'define_func($name)',
      );
    } finally {
      malloc.free(mod);
      malloc.free(nm);
      _b.wasm_functype_delete(funcType);
      calloc.free(params);
      calloc.free(paramsVec);
      calloc.free(resultsVec);
    }
  }

  int _valkind(int wasmtimeKind) =>
      wasmtimeKind == WASMTIME_I64 ? wasm_valkind_enum.WASM_I64.value : wasm_valkind_enum.WASM_I32.value;

  // ── Export calls ────────────────────────────────────────────────────────────

  int _callInt(String name, List<int> args) {
    final namePtr = name.toNativeUtf8();
    final externOut = calloc<wasmtime_extern_t>();
    final funcCopy = calloc<wasmtime_func_t>();
    final argsPtr = calloc<wasmtime_val_t>(args.isEmpty ? 1 : args.length);
    final resultsPtr = calloc<wasmtime_val_t>(1);
    final trap = calloc<Pointer<wasm_trap_t>>();
    try {
      final found = _b.wasmtime_instance_export_get(
          _context, _instance, namePtr.cast<Char>(), namePtr.length, externOut);
      if (!found || externOut.ref.kind != WASMTIME_EXTERN_FUNC) {
        throw WasmRunError('export not found or not a function: $name');
      }
      funcCopy.ref = externOut.ref.of.func;
      for (var i = 0; i < args.length; i++) {
        (argsPtr + i).ref
          ..kind = WASMTIME_I32
          ..of.i32 = args[i];
      }
      final err = _b.wasmtime_func_call(
          _context, funcCopy, argsPtr, args.length, resultsPtr, 1, trap);
      if (err != nullptr) throw WasmRunError(_takeError(err, name));
      if (trap.value != nullptr) throw WasmRunError(_takeTrap(trap.value));
      final r = resultsPtr.ref;
      return r.kind == WASMTIME_I64 ? r.of.i64 : r.of.i32;
    } finally {
      malloc.free(namePtr);
      calloc.free(externOut);
      calloc.free(funcCopy);
      calloc.free(argsPtr);
      calloc.free(resultsPtr);
      calloc.free(trap);
    }
  }

  // ── Memory ──────────────────────────────────────────────────────────────────

  Pointer<wasmtime_memory_t> _memoryHandle() {
    if (_memory != nullptr) return _memory;
    final namePtr = 'memory'.toNativeUtf8();
    final externOut = calloc<wasmtime_extern_t>();
    try {
      final found = _b.wasmtime_instance_export_get(
          _context, _instance, namePtr.cast<Char>(), namePtr.length, externOut);
      if (!found || externOut.ref.kind != WASMTIME_EXTERN_MEMORY) {
        throw const WasmRunError('module does not export "memory"');
      }
      _memory = calloc<wasmtime_memory_t>();
      _memory.ref = externOut.ref.of.memory;
      return _memory;
    } finally {
      malloc.free(namePtr);
      calloc.free(externOut);
    }
  }

  Uint8List _memoryView() {
    final mem = _memoryHandle();
    final base = _b.wasmtime_memory_data(_context, mem);
    final size = _b.wasmtime_memory_data_size(_context, mem);
    return base.asTypedList(size);
  }

  Uint8List _readMemory(int ptr, int len) {
    final v = _memoryView();
    if (ptr < 0 || len < 0 || ptr + len > v.length) {
      throw WasmRunError('OOB read ptr=$ptr len=$len size=${v.length}');
    }
    return Uint8List.fromList(v.sublist(ptr, ptr + len));
  }

  void _writeMemory(int ptr, Uint8List bytes) {
    final v = _memoryView();
    if (ptr < 0 || ptr + bytes.length > v.length) {
      throw WasmRunError('OOB write ptr=$ptr len=${bytes.length}');
    }
    v.setRange(ptr, ptr + bytes.length, bytes);
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

  void _applyFuel() {
    final err = _b.wasmtime_context_set_fuel(_context, fuelLimit);
    _check(err, 'set_fuel');
  }

  void _check(Pointer<wasmtime_error_t> err, String op,
      {Pointer<wasm_trap_t>? trap}) {
    if (err != nullptr) throw WasmRunError(_takeError(err, op));
    if (trap != null && trap != nullptr) throw WasmRunError(_takeTrap(trap));
  }

  String _takeError(Pointer<wasmtime_error_t> err, String op) {
    final vec = calloc<wasm_byte_vec_t>();
    try {
      _b.wasmtime_error_message(err, vec);
      final msg = _readVec(vec);
      _b.wasm_byte_vec_delete(vec);
      _b.wasmtime_error_delete(err);
      return '$op: $msg';
    } finally {
      calloc.free(vec);
    }
  }

  String _takeTrap(Pointer<wasm_trap_t> trap) {
    final vec = calloc<wasm_byte_vec_t>();
    try {
      _b.wasm_trap_message(trap, vec);
      final msg = _readVec(vec);
      _b.wasm_byte_vec_delete(vec);
      _b.wasm_trap_delete(trap);
      return 'trap: $msg';
    } finally {
      calloc.free(vec);
    }
  }

  String _readVec(Pointer<wasm_byte_vec_t> vec) {
    final size = vec.ref.size;
    if (size == 0 || vec.ref.data == nullptr) return '';
    return String.fromCharCodes(vec.ref.data.cast<Uint8>().asTypedList(size));
  }
}
