/// In-process mock of the HelloHQ host ABI for local plugin testing.
///
/// Lets authors exercise a plugin's host calls without running the full app:
/// seed fixture data and a permission grant list, then resolve the same
/// permission-gated reads the real host exposes.
///
/// The [MockHost.resolve] method implements the exact `hq_read` JSON protocol
/// the production `PluginSyncBridge` answers — same request shape
/// (`{method, portfolio_id?}`), same response shape (`{ok, data}` /
/// `{ok:false, error}`), same data shapes (`PluginSyncReader`), and the same
/// gate semantics (deny > grant, portfolio scope filtering). A plugin that
/// works against this mock works against the real host.
library;

import 'dart:convert';

// ─────────────────────────────────────────────────────────────────────────────
// Fixture data
// ─────────────────────────────────────────────────────────────────────────────

class MockPortfolio {
  final String id;
  final String name;
  final List<MockSheet> sheets;

  /// currency id → aggregated total (the `read:aggregated_values` result).
  final Map<String, double> totals;

  const MockPortfolio(
    this.id,
    this.name, {
    this.sheets = const [],
    this.totals = const {},
  });
}

class MockSheet {
  final String id;
  final String name;

  /// 'asset' or 'debt'.
  final String type;
  final List<MockSection> sections;

  const MockSheet(this.id, this.name, this.type, {this.sections = const []});
}

class MockSection {
  final String id;
  final String name;
  final List<MockItem> items;

  const MockSection(this.id, this.name, {this.items = const []});
}

class MockItem {
  final String id;
  final String name;

  const MockItem(this.id, this.name);
}

class MockCurrency {
  final String id;
  final String name;
  final String symbol;
  final num rate;

  const MockCurrency(this.id, this.name, this.symbol, this.rate);
}

/// An event the plugin emitted via `emit_event` (captured for assertions).
class EmittedEvent {
  final String name;
  final String payload;
  const EmittedEvent(this.name, this.payload);
  @override
  String toString() => 'EmittedEvent($name, $payload)';
}

// ─────────────────────────────────────────────────────────────────────────────
// Permission gate (faithful to PluginPermissionGate)
// ─────────────────────────────────────────────────────────────────────────────

/// A minimal, faithful copy of the host's permission gate: deny beats grant,
/// and a grant may carry a `{portfolios: [...]}` scope.
class MockGate {
  final List<Map<String, dynamic>> _granted;
  final Set<String> _denied;

  MockGate({
    List<Map<String, dynamic>> granted = const [],
    Set<String> denied = const {},
  }) : _granted = granted,
       _denied = denied;

  /// Convenience: grant a flat list of permission ids (no scope).
  factory MockGate.allow(Iterable<String> ids) =>
      MockGate(granted: [for (final id in ids) {'id': id}]);

  bool isGranted(String perm) =>
      !_denied.contains(perm) && _granted.any((g) => g['id'] == perm);

  bool isAllowed(String perm, {String? portfolioId}) {
    if (_denied.contains(perm)) return false;
    return _granted.any((g) {
      if (g['id'] != perm) return false;
      final scope = g['scope'] as Map<String, dynamic>?;
      if (scope == null) return true;
      final allowed = scope['portfolios'];
      if (allowed is List) {
        return portfolioId != null && allowed.contains(portfolioId);
      }
      return true;
    });
  }

  /// Allowed portfolio ids for [perm], or null when unrestricted.
  Set<String>? allowedPortfolios(String perm) {
    if (!isGranted(perm)) return <String>{};
    final union = <String>{};
    for (final g in _granted.where((g) => g['id'] == perm)) {
      final scope = g['scope'] as Map<String, dynamic>?;
      final list = scope?['portfolios'];
      if (scope == null || list is! List) return null; // unrestricted
      union.addAll(list.map((e) => e.toString()));
    }
    return union;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AI backend mock
// ─────────────────────────────────────────────────────────────────────────────

/// Callback invoked when a plugin calls `ai:complete`.
///
/// [messages] is the list of `{"role","content"}` objects from the request.
/// [opts] is the `opts` map (`max_tokens`, optional `temperature`).
/// Return the text the mock AI should reply with.
typedef MockAiCompleteCallback = String Function(
  List<dynamic> messages,
  Map<String, dynamic> opts,
);

/// Returns a canned response, cycling the last entry when exhausted.
MockAiCompleteCallback cannedResponses(List<String> responses) {
  assert(responses.isNotEmpty, 'cannedResponses requires at least one entry');
  var index = 0;
  return (_, __) {
    final r = responses[index];
    if (index < responses.length - 1) index++;
    return r;
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Mock host
// ─────────────────────────────────────────────────────────────────────────────

class MockHost {
  final MockGate gate;
  final List<MockPortfolio> portfolios;
  final List<MockCurrency> currencies;

  /// Handles `ai:complete` requests. If null, returns a `not configured` error.
  /// Requires `ai:inference` in the grant list.
  final MockAiCompleteCallback? onAiComplete;

  /// Events the plugin pushed via `emit_event`, in order.
  final List<EmittedEvent> emittedEvents = [];

  MockHost({
    MockGate? gate,
    Iterable<String> granted = const [],
    this.portfolios = const [],
    this.currencies = const [],
    this.onAiComplete,
  }) : gate = gate ?? MockGate.allow(granted);

  /// Resolve an `hq_read` request exactly as the production bridge does.
  ///
  /// Request: `{"method": "read:portfolio_names", "portfolio_id": "ptf_a"?}`.
  /// Response: `{"ok": true, "data": ...}` or `{"ok": false, "error": ...}`.
  String resolve(String requestJson) {
    final Map<String, dynamic> req;
    try {
      final decoded = jsonDecode(requestJson);
      if (decoded is! Map<String, dynamic>) return _err('bad_request');
      req = decoded;
    } catch (_) {
      return _err('bad_request');
    }
    final method = req['method'];
    if (method is! String) return _err('bad_request');
    final portfolioId = req['portfolio_id'] as String?;

    switch (method) {
      case 'read:portfolio_names':
        if (!gate.isGranted(method)) return _err('denied:$method');
        return _ok(_portfolioNames());
      case 'read:sheet_structure':
        final denied = _gatePortfolio(method, portfolioId);
        if (denied != null) return denied;
        return _ok({'portfolios': _sheetStructure(method, portfolioId)});
      case 'read:asset_count':
        final denied = _gatePortfolio(method, portfolioId);
        if (denied != null) return denied;
        return _ok({'portfolios': _assetCounts(method, portfolioId)});
      case 'read:currency_rates':
        if (!gate.isAllowed(method)) return _err('denied:$method');
        return _ok([
          for (final c in currencies)
            {'id': c.id, 'name': c.name, 'symbol': c.symbol, 'rate': c.rate},
        ]);
      case 'read:aggregated_values':
        final denied = _gatePortfolio(method, portfolioId);
        if (denied != null) return denied;
        return _ok({'portfolios': _aggregated(method, portfolioId)});
      case 'ai:complete':
        if (!gate.isGranted('ai:inference')) return _err('denied:ai:inference');
        final callback = onAiComplete;
        if (callback == null) {
          return _err('ai:inference: no mock AI backend configured — pass onAiComplete to MockHost');
        }
        final messages = (req['messages'] as List?) ?? const <dynamic>[];
        final opts = (req['opts'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
        final content = callback(messages, opts);
        return _ok({
          'content': content,
          'input_tokens': 42,
          'output_tokens': content.split(' ').length,
          'model': 'claude-sonnet-4-6',
        });
      default:
        return _err('unknown_method:$method');
    }
  }

  /// Record an event the plugin emitted. Wire this as the `emit_event` sink
  /// when driving a real wasm runner.
  void emit(String name, String payload) =>
      emittedEvents.add(EmittedEvent(name, payload));

  // ── Per-method builders (shapes mirror PluginSyncReader) ──────────────────

  List<Map<String, dynamic>> _portfolioNames() {
    final allowed = gate.allowedPortfolios('read:portfolio_names');
    return [
      for (final p in portfolios)
        if (allowed == null || allowed.contains(p.id))
          {'id': p.id, 'name': p.name},
    ];
  }

  List<Map<String, dynamic>> _sheetStructure(String perm, String? portfolioId) {
    return [
      for (final p in _scoped(perm, portfolioId))
        {
          'id': p.id,
          'sheets': [
            for (final s in p.sheets)
              {
                'id': s.id,
                'name': s.name,
                'sheet_type': s.type,
                'sections': [
                  for (final sec in s.sections)
                    {
                      'id': sec.id,
                      'name': sec.name,
                      'items': [
                        for (final it in sec.items)
                          {'id': it.id, 'name': it.name},
                      ],
                    },
                ],
              },
          ],
        },
    ];
  }

  List<Map<String, dynamic>> _assetCounts(String perm, String? portfolioId) {
    final out = <Map<String, dynamic>>[];
    for (final p in _scoped(perm, portfolioId)) {
      var asset = 0, debt = 0;
      for (final s in p.sheets) {
        final n = s.sections.fold<int>(0, (a, sec) => a + sec.items.length);
        if (s.type == 'debt') {
          debt += n;
        } else {
          asset += n;
        }
      }
      out.add({
        'id': p.id,
        'asset_items': asset,
        'debt_items': debt,
        'total_items': asset + debt,
      });
    }
    return out;
  }

  List<Map<String, dynamic>> _aggregated(String perm, String? portfolioId) {
    return [
      for (final p in _scoped(perm, portfolioId))
        {
          'id': p.id,
          'totals': [
            for (final e in p.totals.entries)
              {'currency_id': e.key, 'total': e.value},
          ],
        },
    ];
  }

  // ── Gate helpers ──────────────────────────────────────────────────────────

  String? _gatePortfolio(String perm, String? portfolioId) {
    if (portfolioId != null) {
      return gate.isAllowed(perm, portfolioId: portfolioId)
          ? null
          : _err('denied:$perm');
    }
    return gate.isGranted(perm) ? null : _err('denied:$perm');
  }

  List<MockPortfolio> _scoped(String perm, String? portfolioId) {
    if (portfolioId != null) {
      return portfolios.where((p) => p.id == portfolioId).toList();
    }
    final allowed = gate.allowedPortfolios(perm);
    return [
      for (final p in portfolios)
        if (allowed == null || allowed.contains(p.id)) p,
    ];
  }

  String _ok(Object? data) => jsonEncode({'ok': true, 'data': data});
  String _err(String error) => jsonEncode({'ok': false, 'error': error});
}

// ─────────────────────────────────────────────────────────────────────────────
// Tier 1 sidecar mock (NDJSON protocol)
// ─────────────────────────────────────────────────────────────────────────────

/// Callback for mocking network fetch in [MockSidecarHost].
///
/// Receives the full request map and returns a response map with keys
/// `status` (int), `headers` (Map<String, dynamic>), and `body` (String).
typedef MockNetworkCallback = Map<String, dynamic> Function(
  Map<String, dynamic> request,
);

/// An in-process mock of the HelloHQ host-side NDJSON protocol for Tier 1
/// (Python sidecar) plugin testing.
///
/// During a sidecar test the plugin sends synchronous host-call messages on
/// stdout and blocks on a stdin `readline()`.  [MockSidecarHost] processes
/// these messages and returns mock responses so you can drive a sidecar plugin
/// without the real HelloHQ app.
///
/// Usage with [Process]:
/// ```dart
/// final mock = MockSidecarHost(
///   granted: ['read:portfolio_names', 'ai:inference'],
///   onAiComplete: cannedResponses(['Great portfolio!']),
/// );
/// final process = await Process.start('python3', ['plugin.py']);
/// await for (final line in process.stdout.transform(utf8.decoder).transform(const LineSplitter())) {
///   final response = mock.handleLine(line);
///   if (response != null) process.stdin.writeln(response);
/// }
/// ```
class MockSidecarHost {
  final MockGate gate;

  /// Handles `ai_complete` requests. Requires `ai:inference` in grants.
  final MockAiCompleteCallback? onAiComplete;

  /// Handles `http_request` calls. Requires `network:fetch` in grants.
  /// Defaults to returning HTTP 403 for every request if not provided.
  final MockNetworkCallback? onNetworkFetch;

  /// In-memory key-value store backing `storage_get/set/delete`.
  /// Requires `plugin:storage` in grants.
  /// Pre-seed it before the test or inspect it afterwards.
  final Map<String, String> storage;

  MockSidecarHost({
    MockGate? gate,
    Iterable<String> granted = const [],
    this.onAiComplete,
    this.onNetworkFetch,
    Map<String, String>? storage,
  }) : gate = gate ?? MockGate.allow(granted),
       storage = storage ?? {};

  /// Process one NDJSON line from the plugin's stdout.
  ///
  /// Returns the NDJSON response string that should be written to the plugin's
  /// stdin, or `null` if the line is not a recognised host-call request (e.g.
  /// a lifecycle message that needs no synchronous reply, or a malformed line).
  String? handleLine(String line) {
    final Map<String, dynamic> msg;
    try {
      final decoded = jsonDecode(line);
      if (decoded is! Map<String, dynamic>) return null;
      msg = decoded;
    } catch (_) {
      return null;
    }

    final type = msg['type'] as String?;
    final seq = msg['seq'];
    if (type == null || seq == null) return null;

    switch (type) {
      case 'ai_complete':
        return _handleAiComplete(msg, seq);
      case 'storage_get':
        return _handleStorageGet(msg, seq);
      case 'storage_set':
        return _handleStorageSet(msg, seq);
      case 'storage_delete':
        return _handleStorageDelete(msg, seq);
      case 'http_request':
        return _handleHttpRequest(msg, seq);
      default:
        // Lifecycle messages (ready, result, error, event) need no synchronous reply.
        return null;
    }
  }

  // ── ai_complete ────────────────────────────────────────────────────────────

  String _handleAiComplete(Map<String, dynamic> msg, dynamic seq) {
    if (!gate.isGranted('ai:inference')) {
      return _errResp('ai_response', seq, 'denied:ai:inference', 'permission_denied');
    }
    final callback = onAiComplete;
    if (callback == null) {
      return _errResp('ai_response', seq,
          'ai:inference: no mock AI backend configured — pass onAiComplete to MockSidecarHost',
          'execution_failed');
    }
    final messages = (msg['messages'] as List?) ?? const <dynamic>[];
    final opts = (msg['opts'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final content = callback(messages, opts);
    return jsonEncode({
      'type': 'ai_response',
      'seq': seq,
      'content': content,
      'usage': {
        'input_tokens': 42,
        'output_tokens': content.split(' ').length,
        'model': 'claude-sonnet-4-6',
      },
    });
  }

  // ── storage ────────────────────────────────────────────────────────────────

  String _handleStorageGet(Map<String, dynamic> msg, dynamic seq) {
    if (!gate.isGranted('plugin:storage')) {
      return _errResp('storage_response', seq, 'denied:plugin:storage', 'permission_denied');
    }
    final key = msg['key'] as String?;
    if (key == null) {
      return _errResp('storage_response', seq, 'missing key', 'invalid_input');
    }
    return jsonEncode({'type': 'storage_response', 'seq': seq, 'value': storage[key]});
  }

  String _handleStorageSet(Map<String, dynamic> msg, dynamic seq) {
    if (!gate.isGranted('plugin:storage')) {
      return _errResp('storage_response', seq, 'denied:plugin:storage', 'permission_denied');
    }
    final key = msg['key'] as String?;
    final value = msg['value'] as String?;
    if (key == null || value == null) {
      return _errResp('storage_response', seq, 'missing key or value', 'invalid_input');
    }
    storage[key] = value;
    return jsonEncode({'type': 'storage_response', 'seq': seq, 'ok': true});
  }

  String _handleStorageDelete(Map<String, dynamic> msg, dynamic seq) {
    if (!gate.isGranted('plugin:storage')) {
      return _errResp('storage_response', seq, 'denied:plugin:storage', 'permission_denied');
    }
    final key = msg['key'] as String?;
    if (key == null) {
      return _errResp('storage_response', seq, 'missing key', 'invalid_input');
    }
    final deleted = storage.remove(key) != null ? 1 : 0;
    return jsonEncode({'type': 'storage_response', 'seq': seq, 'deleted': deleted});
  }

  // ── network ────────────────────────────────────────────────────────────────

  String _handleHttpRequest(Map<String, dynamic> msg, dynamic seq) {
    if (!gate.isGranted('network:fetch')) {
      return _errResp('http_response', seq, 'denied:network:fetch', 'permission_denied');
    }
    final callback = onNetworkFetch;
    final Map<String, dynamic> resp;
    if (callback != null) {
      resp = callback(msg);
    } else {
      // Default stub: 403 with an explanatory body.
      resp = {
        'status': 403,
        'headers': <String, dynamic>{},
        'body': 'mock: no onNetworkFetch callback configured in MockSidecarHost',
      };
    }
    return jsonEncode({
      'type': 'http_response',
      'seq': seq,
      'status': resp['status'] ?? 200,
      'headers': resp['headers'] ?? <String, dynamic>{},
      'body': resp['body'] ?? '',
    });
  }

  // ── helpers ────────────────────────────────────────────────────────────────

  String _errResp(String type, dynamic seq, String error, String errorCode) =>
      jsonEncode({'type': type, 'seq': seq, 'error': error, 'error_code': errorCode});
}
