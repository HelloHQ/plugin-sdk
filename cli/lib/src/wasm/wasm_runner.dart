import 'dart:convert';
import 'dart:ffi';
import 'dart:typed_data';

import 'package:ffi/ffi.dart';
import 'package:hqplugin/src/wasm/wasmtime_bindings.dart';
import 'package:hqplugin/src/wasm/wasmtime_loader.dart';

/// Resolver for the host capability reads: a `hq_read`-style request JSON
/// (`{"method": "read:portfolio_names", "portfolio_id"?: …}`) → response JSON
/// (`{"ok": true, "data": …}` / `{"ok": false, "error": …}`). Bridges the
/// typed `hellohq:plugin/workspace@0.1.0` imports to the mock host.
typedef HostResolver = String Function(String requestJson);

/// Sink for `hellohq:plugin/events@0.1.0` `emit`: (kind, payload as UTF-8).
typedef EventSink = void Function(String name, String payload);

/// Sink for `hellohq:plugin/log@0.1.0` `write`: (level, message).
typedef LogSink = void Function(String level, String message);

/// Thrown on a Wasm compile / link / instantiate / run failure.
class WasmRunError implements Exception {
  final String message;
  const WasmRunError(this.message);
  @override
  String toString() => 'WasmRunError: $message';
}

/// In-CLI **Component Model** host — a focused port of the host's Tier-2
/// runtime over the Wasmtime C-API bindings. Loads a `hellohq:plugin@0.1.0`
/// component, provides the host capability imports (`log`, `workspace`,
/// `events`; `storage`/`inference` trap until implemented), and runs the
/// `guest.run(list<u8>) -> result<list<u8>, string>` export.
///
/// Wasmtime handles the canonical-ABI lifting/lowering of component values
/// (strings, lists, records, results) — the host works in terms of typed
/// [wasmtime_component_val_t]s, not raw guest memory.
///
/// Fuel-metered so a runaway plugin traps instead of hanging the CLI.
class WasmRunner {
  final HostResolver resolver;
  final EventSink? onEvent;
  final LogSink? onLog;
  final int fuelLimit;

  WasmRunner({
    required this.resolver,
    this.onEvent,
    this.onLog,
    this.fuelLimit = 1000000000,
  });

  WasmtimeBindings get _b => CliWasmtimeLib.bindings;

  static const _pkg = 'hellohq:plugin';
  static const _guest = '$_pkg/guest@0.1.0';

  Pointer<wasm_engine_t> _engine = nullptr;
  Pointer<wasmtime_store_t> _store = nullptr;
  Pointer<wasmtime_context_t> _context = nullptr;
  Pointer<wasmtime_component_t> _component = nullptr;
  Pointer<wasmtime_component_linker_t> _linker = nullptr;
  Pointer<wasmtime_component_instance_t> _instance = nullptr;
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
    final compOut = calloc<Pointer<wasmtime_component_t>>();
    try {
      _check(
        _b.wasmtime_component_new(_engine, buf, wasm.length, compOut),
        'component_new (is this a component? run `hqplugin build` to package one)',
      );
      _component = compOut.value;
    } finally {
      calloc.free(buf);
      calloc.free(compOut);
    }

    _linker = _b.wasmtime_component_linker_new(_engine);
    // First, satisfy *every* import as a trap so instantiation never fails on a
    // missing import (type-only `types`, or unimplemented `storage`/`inference`
    // a plugin might pull in). Then shadow the ones we actually implement with
    // real host functions — last definition wins. A plugin that *calls* an
    // unimplemented capability gets a clean trap, not a link failure.
    _b.wasmtime_component_linker_allow_shadowing(_linker, true);
    _check(
      _b.wasmtime_component_linker_define_unknown_imports_as_traps(
        _linker,
        _component,
      ),
      'define_unknown_imports_as_traps',
    );
    _defineImports();

    _instance = calloc<wasmtime_component_instance_t>();
    _check(
      _b.wasmtime_component_linker_instantiate(
        _linker,
        _context,
        _component,
        _instance,
      ),
      'instantiate',
    );
    _loaded = true;
    _callInit();
  }

  /// Run `guest.run(input) -> result<list<u8>, string>`, returning the decoded
  /// output (the plugin's declarative JSON).
  String runBytes(String input) {
    if (!_loaded) throw const WasmRunError('not loaded');
    _applyFuel();
    final func = _func(_guest, 'run');
    final args = calloc<wasmtime_component_val_t>(1);
    _setU8List(args, utf8.encode(input));
    final results = calloc<wasmtime_component_val_t>(1);
    try {
      _check(
        _b.wasmtime_component_func_call(func, _context, args, 1, results, 1),
        'run',
      );
      if (results.ref.kind != WASMTIME_COMPONENT_RESULT) {
        throw WasmRunError('run: unexpected result kind ${results.ref.kind}');
      }
      final inner = results.ref.of.result.val;
      if (!results.ref.of.result.is_ok) {
        throw WasmRunError('run returned an error: ${_readStringVal(inner)}');
      }
      return utf8.decode(_readU8List(inner));
    } finally {
      _b.wasmtime_component_val_delete(args);
      calloc.free(args);
      _b.wasmtime_component_val_delete(results);
      calloc.free(results);
      calloc.free(func);
    }
  }

  void dispose() {
    for (final cb in _callbacks) {
      cb.close();
    }
    _callbacks.clear();
    if (_instance != nullptr) calloc.free(_instance);
    if (_linker != nullptr) _b.wasmtime_component_linker_delete(_linker);
    if (_component != nullptr) _b.wasmtime_component_delete(_component);
    if (_store != nullptr) _b.wasmtime_store_delete(_store);
    if (_engine != nullptr) _b.wasm_engine_delete(_engine);
    _instance = _linker = _component = _store = _engine = nullptr;
    _context = nullptr;
    _loaded = false;
  }

  // ── Host imports ────────────────────────────────────────────────────────────

  void _defineImports() {
    final root = _b.wasmtime_component_linker_root(_linker);

    final log = _addInstance(root, '$_pkg/log@0.1.0');
    _addFunc(log, 'write', _cb(_hostLogWrite));

    final ws = _addInstance(root, '$_pkg/workspace@0.1.0');
    _addFunc(ws, 'read-portfolio-names', _cb(_hostReadPortfolioNames));
    _addFunc(ws, 'read-currency-rates', _cb(_hostReadCurrencyRates));
    _addFunc(ws, 'read-sheet-structure', _cb(_hostReadSheetStructure));
    _addFunc(ws, 'read-asset-count', _cb(_hostReadAssetCount));
    _addFunc(ws, 'read-aggregated-values', _cb(_hostReadAggregated));
    _addFunc(ws, 'write-external-file', _cb(_hostWriteExternalFile));

    final events = _addInstance(root, '$_pkg/events@0.1.0');
    _addFunc(events, 'emit', _cb(_hostEmit));
  }

  /// Bridge a `hq_read`-style request through [resolver] and decode the reply.
  Map<String, dynamic> _resolve(Map<String, dynamic> req) {
    try {
      final decoded = jsonDecode(resolver(jsonEncode(req)));
      return decoded is Map<String, dynamic>
          ? decoded
          : const {'ok': false, 'error': 'bad_response'};
    } catch (_) {
      return const {'ok': false, 'error': 'bad_response'};
    }
  }

  Pointer<wasmtime_component_val_t> _apiErrorFrom(Map<String, dynamic> r) {
    final code = (r['error'] ?? 'error').toString();
    return _apiError(code, code);
  }

  // log.write(level: enum, message: string)
  void _hostLogWrite(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final level = _readEnumVal(args);
    final message = _readStringVal(args + 1);
    onLog?.call(level, message);
  }

  // read-portfolio-names() -> result<list<portfolio-name>, api-error>
  void _hostReadPortfolioNames(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final r = _resolve({'method': 'read:portfolio_names'});
    if (r['ok'] != true) return _emit(results, _err(_apiErrorFrom(r)));
    final data = (r['data'] as List?) ?? const [];
    final rows = [
      for (final p in data.cast<Map>())
        _record([
          MapEntry('id', _str(_s(p['id']))),
          MapEntry('name', _str(_s(p['name']))),
        ]),
    ];
    _emit(results, _ok(_list(rows)));
  }

  // read-currency-rates() -> result<list<currency-rate>, api-error>
  void _hostReadCurrencyRates(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final r = _resolve({'method': 'read:currency_rates'});
    if (r['ok'] != true) return _emit(results, _err(_apiErrorFrom(r)));
    final data = (r['data'] as List?) ?? const [];
    final rows = [
      for (final c in data.cast<Map>())
        _record([
          MapEntry('id', _str(_s(c['id']))),
          MapEntry('name', _str(_s(c['name']))),
          MapEntry('symbol', _str(_s(c['symbol']))),
          MapEntry('rate', _f64(_d(c['rate']))),
        ]),
    ];
    _emit(results, _ok(_list(rows)));
  }

  // read-sheet-structure(portfolio-id) -> result<sheet-summary, api-error>
  void _hostReadSheetStructure(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final pid = _readStringVal(args);
    final r = _resolve({
      'method': 'read:sheet_structure',
      'portfolio_id': pid,
    });
    if (r['ok'] != true) return _emit(results, _err(_apiErrorFrom(r)));
    final portfolios =
        ((r['data'] as Map?)?['portfolios'] as List?) ?? const [];
    final pf = portfolios.cast<Map>().firstWhere(
          (p) => p['id'] == pid,
          orElse: () => portfolios.isEmpty ? const {} : portfolios.first as Map,
        );
    final sheets = (pf['sheets'] as List?) ?? const [];
    final sheetInfos = [
      for (final s in sheets.cast<Map>())
        _record([
          MapEntry('name', _str(_s(s['name']))),
          MapEntry(
            'sections',
            _list([
              for (final sec
                  in (s['sections'] as List? ?? const []).cast<Map>())
                _str(_s(sec['name'])),
            ]),
          ),
        ]),
    ];
    _emit(
      results,
      _ok(
        _record([
          MapEntry('portfolio-id', _str(_s(pf['id'] ?? pid))),
          MapEntry('sheets', _list(sheetInfos)),
        ]),
      ),
    );
  }

  // read-asset-count(portfolio-id) -> result<asset-count, api-error>
  void _hostReadAssetCount(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final pid = _readStringVal(args);
    final r = _resolve({'method': 'read:asset_count', 'portfolio_id': pid});
    if (r['ok'] != true) return _emit(results, _err(_apiErrorFrom(r)));
    final portfolios =
        ((r['data'] as Map?)?['portfolios'] as List?) ?? const [];
    final pf = portfolios.cast<Map>().firstWhere(
          (p) => p['id'] == pid,
          orElse: () => portfolios.isEmpty ? const {} : portfolios.first as Map,
        );
    final byCategory = [
      _record([
        MapEntry('category', _str('asset')),
        MapEntry('count', _u32(_i(pf['asset_items']))),
      ]),
      _record([
        MapEntry('category', _str('debt')),
        MapEntry('count', _u32(_i(pf['debt_items']))),
      ]),
    ];
    _emit(
      results,
      _ok(
        _record([
          MapEntry('portfolio-id', _str(_s(pf['id'] ?? pid))),
          MapEntry('count-by-category', _list(byCategory)),
        ]),
      ),
    );
  }

  // read-aggregated-values(portfolio-id) -> result<aggregated-summary, api-error>
  void _hostReadAggregated(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final pid = _readStringVal(args);
    final r = _resolve({
      'method': 'read:aggregated_values',
      'portfolio_id': pid,
    });
    if (r['ok'] != true) return _emit(results, _err(_apiErrorFrom(r)));
    final portfolios =
        ((r['data'] as Map?)?['portfolios'] as List?) ?? const [];
    final pf = portfolios.cast<Map>().firstWhere(
          (p) => p['id'] == pid,
          orElse: () => portfolios.isEmpty ? const {} : portfolios.first as Map,
        );
    final totals = [
      for (final t in (pf['totals'] as List? ?? const []).cast<Map>())
        _record([
          MapEntry('category', _str(_s(t['currency_id']))),
          MapEntry('total', _f64(_d(t['total']))),
        ]),
    ];
    _emit(
      results,
      _ok(
        _record([
          MapEntry('portfolio-id', _str(_s(pf['id'] ?? pid))),
          MapEntry('totals', _list(totals)),
        ]),
      ),
    );
  }

  // write-external-file(filename, content) -> result<_, api-error>
  // RESERVED in the WIT (no Tier-2 wiring) — deny cleanly.
  void _hostWriteExternalFile(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    _emit(
      results,
      _err(
        _apiError(
          'permission-denied',
          'write-external-file is not available in hqplugin test',
        ),
      ),
    );
  }

  // emit(plugin-event{kind, payload: list<u8>}) -> result<_, api-error>
  void _hostEmit(
    Pointer<wasmtime_component_val_t> args,
    int nargs,
    Pointer<wasmtime_component_val_t> results,
    int nresults,
  ) {
    final rec = args.ref.of.record;
    String kind = '';
    String payload = '';
    for (var i = 0; i < rec.size; i++) {
      final e = rec.data + i;
      final field = _readName(e.ref.name);
      if (field == 'kind') {
        kind = _readStringVal(_valPtr(e));
      } else if (field == 'payload') {
        payload = utf8.decode(_readU8List(_valPtr(e)));
      }
    }
    onEvent?.call(kind, payload);
    _emit(results, _ok(null));
  }

  /// Pointer to the `val` field of a record entry — it follows the `name`
  /// (`wasm_name_t`, 16 bytes, 8-aligned) field with no padding.
  Pointer<wasmtime_component_val_t> _valPtr(
    Pointer<wasmtime_component_valrecord_entry> entry,
  ) =>
      Pointer<wasmtime_component_val_t>.fromAddress(
        entry.address + sizeOf<wasm_name_t>(),
      );

  // ── Component-value builders ──────────────────────────────────────────────
  // Each returns a freshly calloc'd container the caller takes ownership of:
  // nesting copies the struct by value and frees the container; the top-level
  // value handed to wasmtime is freed by it via `wasmtime_component_val_delete`.

  Pointer<wasmtime_component_val_t> _str(String s) {
    final v = calloc<wasmtime_component_val_t>();
    _setStr(v, s);
    return v;
  }

  void _setStr(Pointer<wasmtime_component_val_t> slot, String s) {
    final bytes = utf8.encode(s);
    final data = malloc<Uint8>(bytes.isEmpty ? 1 : bytes.length);
    if (bytes.isNotEmpty) data.asTypedList(bytes.length).setAll(0, bytes);
    slot.ref.kind = WASMTIME_COMPONENT_STRING;
    slot.ref.of.string
      ..size = bytes.length
      ..data = data.cast();
  }

  Pointer<wasmtime_component_val_t> _f64(double d) {
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_F64;
    v.ref.of.f64 = d;
    return v;
  }

  Pointer<wasmtime_component_val_t> _u32(int n) {
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_U32;
    v.ref.of.u32 = n;
    return v;
  }

  void _setU8List(Pointer<wasmtime_component_val_t> slot, List<int> bytes) {
    final n = bytes.length;
    final data = malloc<wasmtime_component_val>(n == 0 ? 1 : n);
    for (var i = 0; i < n; i++) {
      (data + i).ref
        ..kind = WASMTIME_COMPONENT_U8
        ..of.u8 = bytes[i] & 0xff;
    }
    slot.ref.kind = WASMTIME_COMPONENT_LIST;
    slot.ref.of.list
      ..size = n
      ..data = data;
  }

  Pointer<wasmtime_component_val_t> _record(
    List<MapEntry<String, Pointer<wasmtime_component_val_t>>> fields,
  ) {
    final k = fields.length;
    final data = malloc<wasmtime_component_valrecord_entry>(k);
    for (var i = 0; i < k; i++) {
      final name = utf8.encode(fields[i].key);
      final nd = malloc<Uint8>(name.isEmpty ? 1 : name.length);
      if (name.isNotEmpty) nd.asTypedList(name.length).setAll(0, name);
      (data + i).ref.name
        ..size = name.length
        ..data = nd.cast();
      (data + i).ref.val = fields[i].value.ref;
      calloc.free(fields[i].value);
    }
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_RECORD;
    v.ref.of.record
      ..size = k
      ..data = data;
    return v;
  }

  Pointer<wasmtime_component_val_t> _list(
    List<Pointer<wasmtime_component_val_t>> elems,
  ) {
    final n = elems.length;
    final data = malloc<wasmtime_component_val>(n == 0 ? 1 : n);
    for (var i = 0; i < n; i++) {
      (data + i).ref = elems[i].ref;
      calloc.free(elems[i]);
    }
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_LIST;
    v.ref.of.list
      ..size = n
      ..data = data;
    return v;
  }

  /// `result<T, E>` ok-branch. [payload] is null for a unit (`_`) ok type.
  Pointer<wasmtime_component_val_t> _ok(
      Pointer<wasmtime_component_val_t>? payload) {
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_RESULT;
    v.ref.of.result.is_ok = true;
    v.ref.of.result.val =
        payload == null ? nullptr : _b.wasmtime_component_val_new(payload);
    if (payload != null) calloc.free(payload);
    return v;
  }

  Pointer<wasmtime_component_val_t> _err(
      Pointer<wasmtime_component_val_t> payload) {
    final v = calloc<wasmtime_component_val_t>();
    v.ref.kind = WASMTIME_COMPONENT_RESULT;
    v.ref.of.result.is_ok = false;
    v.ref.of.result.val = _b.wasmtime_component_val_new(payload);
    calloc.free(payload);
    return v;
  }

  Pointer<wasmtime_component_val_t> _apiError(String code, String message) =>
      _record([
        MapEntry('code', _str(code)),
        MapEntry('message', _str(message)),
      ]);

  /// Move [built] into the wasmtime-provided result slot and release its
  /// container. Wasmtime owns (and later deletes) the moved contents.
  void _emit(
    Pointer<wasmtime_component_val_t> resultSlot,
    Pointer<wasmtime_component_val_t> built,
  ) {
    resultSlot.ref = built.ref;
    calloc.free(built);
  }

  // ── Component-value readers ───────────────────────────────────────────────

  String _readStringVal(Pointer<wasmtime_component_val_t> v) =>
      _readName(v.ref.of.string);

  String _readEnumVal(Pointer<wasmtime_component_val_t> v) =>
      _readName(v.ref.of.enumeration);

  String _readName(wasm_name_t n) =>
      n.size == 0 ? '' : utf8.decode(n.data.cast<Uint8>().asTypedList(n.size));

  Uint8List _readU8List(Pointer<wasmtime_component_val_t> v) {
    final l = v.ref.of.list;
    final out = Uint8List(l.size);
    for (var i = 0; i < l.size; i++) {
      out[i] = (l.data + i).ref.of.u8;
    }
    return out;
  }

  // ── Linker / export plumbing ──────────────────────────────────────────────

  Pointer<wasmtime_component_linker_instance_t> _addInstance(
    Pointer<wasmtime_component_linker_instance_t> parent,
    String name,
  ) {
    final np = name.toNativeUtf8();
    final out = calloc<Pointer<wasmtime_component_linker_instance_t>>();
    try {
      _check(
        _b.wasmtime_component_linker_instance_add_instance(
          parent,
          np.cast<Char>(),
          np.length,
          out,
        ),
        'add_instance($name)',
      );
      return out.value;
    } finally {
      malloc.free(np);
      calloc.free(out);
    }
  }

  void _addFunc(
    Pointer<wasmtime_component_linker_instance_t> inst,
    String name,
    NativeCallable<wasmtime_component_func_callback_tFunction> cb,
  ) {
    _callbacks.add(cb);
    final np = name.toNativeUtf8();
    try {
      _check(
        _b.wasmtime_component_linker_instance_add_func(
          inst,
          np.cast<Char>(),
          np.length,
          cb.nativeFunction,
          nullptr,
          nullptr,
        ),
        'add_func($name)',
      );
    } finally {
      malloc.free(np);
    }
  }

  /// Wrap a host handler in a wasmtime component func callback.
  NativeCallable<wasmtime_component_func_callback_tFunction> _cb(
    void Function(
      Pointer<wasmtime_component_val_t> args,
      int nargs,
      Pointer<wasmtime_component_val_t> results,
      int nresults,
    ) handler,
  ) {
    return NativeCallable<
        wasmtime_component_func_callback_tFunction>.isolateLocal(
      (
        Pointer<Void> data,
        Pointer<wasmtime_context_t> ctx,
        Pointer<wasmtime_component_func_type_t> type,
        Pointer<wasmtime_component_val_t> args,
        int nargs,
        Pointer<wasmtime_component_val_t> results,
        int nresults,
      ) {
        handler(args, nargs, results, nresults);
        return nullptr;
      },
    );
  }

  void _callInit() {
    final Pointer<wasmtime_component_func_t> func;
    try {
      func = _func(_guest, 'init');
    } on WasmRunError {
      return; // init is optional
    }
    try {
      _check(
        _b.wasmtime_component_func_call(func, _context, nullptr, 0, nullptr, 0),
        'init',
      );
    } finally {
      calloc.free(func);
    }
  }

  Pointer<wasmtime_component_func_t> _func(String instance, String name) {
    final instIdx = _exportIndex(nullptr, instance);
    if (instIdx == nullptr) {
      throw WasmRunError('export instance not found: $instance');
    }
    final funcIdx = _exportIndex(instIdx, name);
    if (funcIdx == nullptr) {
      _b.wasmtime_component_export_index_delete(instIdx);
      throw WasmRunError('export func not found: $instance#$name');
    }
    final out = calloc<wasmtime_component_func_t>();
    final ok = _b.wasmtime_component_instance_get_func(
      _instance,
      _context,
      funcIdx,
      out,
    );
    _b.wasmtime_component_export_index_delete(instIdx);
    _b.wasmtime_component_export_index_delete(funcIdx);
    if (!ok) {
      calloc.free(out);
      throw WasmRunError('get_func failed: $instance#$name');
    }
    return out;
  }

  Pointer<wasmtime_component_export_index_t> _exportIndex(
    Pointer<wasmtime_component_export_index_t> parent,
    String name,
  ) {
    final np = name.toNativeUtf8();
    try {
      return _b.wasmtime_component_instance_get_export_index(
        _instance,
        _context,
        parent,
        np.cast<Char>(),
        np.length,
      );
    } finally {
      malloc.free(np);
    }
  }

  // ── Misc ──────────────────────────────────────────────────────────────────

  void _applyFuel() {
    _check(_b.wasmtime_context_set_fuel(_context, fuelLimit), 'set_fuel');
  }

  void _check(Pointer<wasmtime_error_t> err, String op) {
    if (err != nullptr) throw WasmRunError(_takeError(err, op));
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

  String _readVec(Pointer<wasm_byte_vec_t> vec) {
    final size = vec.ref.size;
    if (size == 0 || vec.ref.data == nullptr) return '';
    return String.fromCharCodes(vec.ref.data.cast<Uint8>().asTypedList(size));
  }

  // JSON coercion helpers.
  static String _s(Object? v) => v?.toString() ?? '';
  static double _d(Object? v) => v is num ? v.toDouble() : 0.0;
  static int _i(Object? v) => v is num ? v.toInt() : 0;
}
