/// A MOCK of the host's `propose` call (Tier 1 NDJSON `propose` /
/// `propose_response`, docs/plugin/30 section 3) so `hqplugin test --sidecar`
/// can exercise a plugin that proposes holdings and values.
///
/// What it is: an approximation of hellohq's submit service and validator that
/// accepts a valid batch and answers one receipt per proposal, refuses what the
/// host refuses, and uses the host's closed set of reason codes and error codes
/// (`plugin-protocol` `sidecar/host-calls.schema.json`).
///
/// What it is not: the host. Nothing is persisted (state lives in this object
/// and dies with the test run), there is no review UI, no binding, no
/// rejection memory and no workspace. Where it can disagree with the real host,
/// the real host wins: it checks `value.currency` only against the schema's
/// shape (not the host's ISO 4217 and crypto lists), does not check that
/// `source.origin` was fetched this run, and has no persisted rate limit across
/// runs. The outcomes `unchanged` and `suppressed` depend on the person's data;
/// force them per `source_key` with [MockProposer.forcedOutcomes].
library;

import 'dart:convert';

/// The closed set of `reason` codes (host-calls.schema.json `$defs.reason`).
const Set<String> mockProposeReasons = {
  'invalid',
  'too_many',
  'too_large',
  'bad_schema',
  'host_field_supplied',
  'unknown_field',
  'field_not_allowed_for_kind',
  'missing_field',
  'bad_kind',
  'permission_denied',
  'bad_source_key',
  'bad_asset_kind',
  'asset_kind_not_allowed',
  'bad_display_name',
  'bad_instrument',
  'bad_quantity',
  'bad_value',
  'bad_currency',
  'bad_as_of',
  'as_of_in_future',
  'as_of_too_old',
  'bad_method',
  'bad_source',
  'bad_reference',
  'bad_fetched_at',
  'fetched_at_outside_run',
};

/// The asset kinds a holding may target (the permission's `scope.kinds`).
const Set<String> mockProposeAssetKinds = {
  'stock_ticker',
  'crypto_ticker',
  'crypto_exchange',
  'home',
  'car',
  'precious_metal',
  'domain',
  'loan_mortgage',
};

const Set<String> _methods = {
  'reported_balance',
  'quoted_price',
  'comparable_sales_median',
  'index_adjusted',
  'statement',
};

const Set<String> _hostOwnedFields = {
  'plugin_id',
  'plugin_version',
  'content_hash',
  'trust_tier',
  'run_id',
  'received_at',
  'host_observed_origins',
  'host_observed',
  'source_observed',
  'dedup_key',
  'source_digest',
};

const int _maxProposals = 200;
const int _maxHoldings = 50;
const int _maxPayloadBytes = 256 * 1024;
const int _maxPending = 500;
const int _callsPerMinute = 10;
const int _callsPerDay = 100;

/// The fixed `error` text per error code, as the host sends it.
const Map<String, String> _errorText = {
  'permission_denied': 'propose permission is not granted for this plugin.',
  'bad_request': 'The proposal batch is not valid.',
  'too_many': 'The proposal batch has too many proposals.',
  'too_large': 'The proposal batch is too large.',
  'rate_limit_exceeded': 'Too many propose calls; try again later.',
  'quota_exceeded': 'Too many suggestions are waiting for review.',
  'workspace_unavailable': 'No matching workspace is open.',
  'host_error': 'Internal host error.',
};

/// Mock propose-only write service. One instance is one plugin run.
class MockProposer {
  MockProposer({
    DateTime Function()? clock,
    DateTime? runStart,
    Map<String, String>? forcedOutcomes,
  }) : _clock = clock ?? DateTime.now,
       forcedOutcomes = forcedOutcomes ?? {} {
    this.runStart = (runStart ?? _clock()).toUtc();
  }

  final DateTime Function() _clock;

  /// Start of the run: `source.fetched_at` must fall between this and now
  /// (give or take 5 seconds), as the host's run window requires.
  late final DateTime runStart;

  /// Force the receipt for a proposal by its `source_key`: one of `duplicate`,
  /// `superseded_older`, `unchanged`, `suppressed`. Applies only to a proposal
  /// that passed validation. Lets a test reach outcomes that depend on data the
  /// mock does not have (what the person approved or declined earlier).
  final Map<String, String> forcedOutcomes;

  /// Every proposal this run queued, in order, as the plugin sent it. For
  /// assertions and for the CLI to print; nothing here outlives the process.
  final List<Map<String, dynamic>> queued = [];

  final Map<String, String> _pendingByKey = {}; // kind|source_key -> dedup key
  final Set<String> _pendingDedup = {};
  final List<DateTime> _calls = [];

  /// Answers one `propose` message with the `propose_response` map (without
  /// `type` and `seq`). [holdingKinds] and [valuationKinds] are the
  /// `scope.kinds` of the plugin's grant for each id, or null when it holds no
  /// valid grant for it.
  Map<String, dynamic> handle(
    Map<String, dynamic> msg, {
    required Set<String>? holdingKinds,
    required Set<String>? valuationKinds,
  }) {
    if (holdingKinds == null && valuationKinds == null) {
      return _refuse('permission_denied');
    }
    final batch = msg['batch'];
    if (batch is! Map) return _refuse('bad_request');

    final now = _clock().toUtc();
    if (!_acquireCall(now)) return _refuse('rate_limit_exceeded');

    final checked = _validate(
      batch,
      now,
      holdingKinds: holdingKinds,
      valuationKinds: valuationKinds,
    );
    if (checked.batchReason != null) {
      final reason = checked.batchReason!;
      return switch (reason) {
        'too_many' => _refuse('too_many', reason: reason),
        'too_large' => _refuse('too_large', reason: reason),
        _ => _refuse('bad_request', reason: reason),
      };
    }

    // Decide every receipt first; only commit when the pending quota holds, so
    // a batch that would overflow it is refused whole (as the host's rollback).
    final receipts = <Map<String, dynamic>>[];
    final newPending = <(String, String, Map<String, dynamic>)>[];
    final seenInBatch = <String>{};
    var newlyPending = 0;
    var supersededCount = 0;
    final proposals = batch['proposals'] as List;
    for (var i = 0; i < proposals.length; i++) {
      final rejectedReason = checked.proposalReasons[i];
      if (rejectedReason != null) {
        receipts.add({
          'index': i,
          'outcome': 'invalid',
          'reason': rejectedReason,
        });
        continue;
      }
      final p = Map<String, dynamic>.from(proposals[i] as Map);
      final slot = '${p['kind']}|${p['source_key']}';
      final dedup = _dedupKey(p);
      final forced = forcedOutcomes[p['source_key']];
      var outcome = 'queued';
      if (forced != null &&
          const {
            'duplicate',
            'superseded_older',
            'unchanged',
            'suppressed',
          }.contains(forced)) {
        outcome = forced;
      } else if (_pendingDedup.contains(dedup) || !seenInBatch.add(dedup)) {
        outcome = 'duplicate';
      } else if (_pendingByKey.containsKey(slot)) {
        outcome = 'superseded_older';
      }
      if (outcome == 'queued' || outcome == 'superseded_older') {
        newPending.add((slot, dedup, p));
        if (outcome == 'superseded_older') supersededCount++;
        newlyPending++;
      }
      receipts.add({'index': i, 'outcome': outcome});
    }
    if (_pendingDedup.length + newlyPending - supersededCount > _maxPending) {
      return _refuse('quota_exceeded');
    }
    for (final (slot, dedup, p) in newPending) {
      final older = _pendingByKey[slot];
      if (older != null) _pendingDedup.remove(older);
      _pendingByKey[slot] = dedup;
      _pendingDedup.add(dedup);
      queued.add(p);
    }
    return {'receipts': receipts};
  }

  Map<String, dynamic> _refuse(String code, {String? reason}) => {
    'error': _errorText[code],
    'error_code': code,
    'reason': ?reason,
  };

  /// 10 calls a minute and 100 a day; a refused call is not counted.
  bool _acquireCall(DateTime now) {
    _calls.removeWhere((t) => now.difference(t) >= const Duration(days: 1));
    final lastMinute = _calls
        .where((t) => now.difference(t) < const Duration(minutes: 1))
        .length;
    if (lastMinute >= _callsPerMinute || _calls.length >= _callsPerDay) {
      return false;
    }
    _calls.add(now);
    return true;
  }

  /// The identity of a proposal, from the same inputs as the host's dedup key
  /// (kind, source key, as_of to the second, canonical value, currency,
  /// quantity), without the HMAC.
  String _dedupKey(Map<String, dynamic> p) {
    final asOf = parseMockRfc3339Utc(p['as_of'] as String)!;
    final value = p['value'] as Map?;
    final quantity = p['quantity'] as Map?;
    return [
      p['kind'],
      p['source_key'],
      asOf.millisecondsSinceEpoch ~/ 1000,
      if (value != null)
        canonicalMockDecimal(value['amount'] as String)
      else
        '',
      if (value != null) value['currency'] else '',
      if (quantity != null)
        canonicalMockDecimal(quantity['amount'] as String)
      else
        '',
    ].join('\u0000');
  }

  // ── validation (an approximation of PluginProposalValidator) ──────────────

  static const _commonKeys = {
    'kind',
    'source_key',
    'value',
    'quantity',
    'as_of',
    'method',
    'source',
  };
  static const _holdingOnlyKeys = {'asset_kind', 'display_name', 'instrument'};

  _Checked _validate(
    Map batch,
    DateTime now, {
    required Set<String>? holdingKinds,
    required Set<String>? valuationKinds,
  }) {
    _Checked whole(String reason) => _Checked(batchReason: reason);
    final shape = _scan(batch);
    if (shape.malformed) return whole('invalid');
    if (shape.bytes > _maxPayloadBytes) return whole('too_large');
    if (shape.hostField) return whole('host_field_supplied');
    for (final key in batch.keys) {
      if (key != 'schema' && key != 'proposals') return whole('unknown_field');
    }
    if (batch['schema'] != 'hellohq.proposal-batch@1') {
      return whole('bad_schema');
    }
    final proposals = batch['proposals'];
    if (proposals is! List) return whole('invalid');
    if (proposals.length > _maxProposals) return whole('too_many');
    final holdings = proposals
        .where((p) => p is Map && p['kind'] == 'holding')
        .length;
    if (holdings > _maxHoldings) return whole('too_many');

    final reasons = <int, String>{};
    for (var i = 0; i < proposals.length; i++) {
      try {
        _validateProposal(
          proposals[i],
          now,
          holdingKinds: holdingKinds,
          valuationKinds: valuationKinds,
        );
      } on _Reject catch (r) {
        reasons[i] = r.reason;
      } on Object {
        reasons[i] = 'invalid';
      }
    }
    return _Checked(proposalReasons: reasons);
  }

  void _validateProposal(
    Object? raw,
    DateTime now, {
    required Set<String>? holdingKinds,
    required Set<String>? valuationKinds,
  }) {
    if (raw is! Map) throw const _Reject('invalid');
    final kind = raw['kind'];
    if (kind != 'holding' && kind != 'valuation') {
      throw const _Reject('bad_kind');
    }
    if ((kind == 'holding' ? holdingKinds : valuationKinds) == null) {
      throw const _Reject('permission_denied');
    }
    for (final entry in raw.entries) {
      final key = entry.key;
      if (key is! String) throw const _Reject('invalid');
      if (_commonKeys.contains(key)) continue;
      if (_holdingOnlyKeys.contains(key)) {
        if (kind == 'holding' || entry.value == null) continue;
        throw const _Reject('field_not_allowed_for_kind');
      }
      throw const _Reject('unknown_field');
    }
    final sourceKey = raw['source_key'];
    if (sourceKey == null) throw const _Reject('missing_field');
    if (sourceKey is! String || !_sourceKey.hasMatch(sourceKey)) {
      throw const _Reject('bad_source_key');
    }
    if (kind == 'holding') {
      final assetKind = raw['asset_kind'];
      if (assetKind == null) throw const _Reject('missing_field');
      if (assetKind is! String || !mockProposeAssetKinds.contains(assetKind)) {
        throw const _Reject('bad_asset_kind');
      }
      if (!holdingKinds!.contains(assetKind)) {
        throw const _Reject('asset_kind_not_allowed');
      }
      _displayName(raw['display_name']);
      _instrument(raw['instrument']);
    }
    _quantity(raw['quantity']);
    _value(raw['value'], required: kind == 'valuation');
    _asOf(raw['as_of'], now);
    final method = raw['method'];
    if (method != null && (method is! String || !_methods.contains(method))) {
      throw const _Reject('bad_method');
    }
    _source(raw['source'], now);
  }

  void _displayName(Object? raw) {
    if (raw == null) throw const _Reject('missing_field');
    if (raw is! String ||
        raw.isEmpty ||
        raw.runes.length > 80 ||
        raw != raw.trim() ||
        !_isPlainText(raw) ||
        _looksLikeUrl(raw, bareDomains: true)) {
      throw const _Reject('bad_display_name');
    }
  }

  void _instrument(Object? raw) {
    if (raw == null) return;
    const bad = _Reject('bad_instrument');
    if (raw is! Map) throw bad;
    for (final key in raw.keys) {
      if (key != 'symbol' && key != 'chain' && key != 'figi') throw bad;
    }
    void field(String key, RegExp pattern) {
      final v = raw[key];
      if (v != null && (v is! String || !pattern.hasMatch(v))) throw bad;
    }

    field('symbol', RegExp(r'^[A-Za-z0-9:._/-]{1,32}$'));
    field('chain', RegExp(r'^[a-z0-9._-]{1,32}$'));
    field('figi', RegExp(r'^[A-Z0-9]{12}$'));
  }

  void _quantity(Object? raw) {
    if (raw == null) return;
    const bad = _Reject('bad_quantity');
    if (raw is! Map) throw bad;
    for (final key in raw.keys) {
      if (key != 'amount' && key != 'unit') throw bad;
    }
    final unit = raw['unit'];
    if (!_validDecimal(raw['amount']) ||
        unit is! String ||
        !RegExp(r'^[A-Za-z0-9._-]{1,16}$').hasMatch(unit)) {
      throw bad;
    }
  }

  void _value(Object? raw, {required bool required}) {
    if (raw == null) {
      if (required) throw const _Reject('missing_field');
      return;
    }
    const bad = _Reject('bad_value');
    if (raw is! Map) throw bad;
    for (final key in raw.keys) {
      if (key != 'amount' && key != 'currency') throw bad;
    }
    if (!_validDecimal(raw['amount'])) throw bad;
    final currency = raw['currency'];
    if (currency is! String ||
        !RegExp(r'^[A-Z0-9]{3,16}$').hasMatch(currency)) {
      throw const _Reject('bad_currency');
    }
  }

  void _asOf(Object? raw, DateTime now) {
    if (raw == null) throw const _Reject('missing_field');
    final parsed = raw is String ? parseMockRfc3339Utc(raw) : null;
    if (parsed == null) throw const _Reject('bad_as_of');
    if (parsed.isAfter(now.add(const Duration(minutes: 5)))) {
      throw const _Reject('as_of_in_future');
    }
    final oldest = DateTime.utc(
      now.year - 10,
      now.month,
      now.day,
      now.hour,
      now.minute,
      now.second,
    );
    if (parsed.isBefore(oldest)) throw const _Reject('as_of_too_old');
  }

  void _source(Object? raw, DateTime now) {
    if (raw == null) throw const _Reject('missing_field');
    const bad = _Reject('bad_source');
    if (raw is! Map) throw bad;
    for (final key in raw.keys) {
      if (key != 'origin' &&
          key != 'reference' &&
          key != 'fetched_at' &&
          key != 'price_origin') {
        throw bad;
      }
    }
    final origin = raw['origin'];
    final reference = raw['reference'];
    final fetchedAt = raw['fetched_at'];
    final priceOrigin = raw['price_origin'];
    if (origin == null || reference == null || fetchedAt == null) {
      throw const _Reject('missing_field');
    }
    if (origin is! String || !_hostName.hasMatch(origin)) throw bad;
    if (priceOrigin != null &&
        (priceOrigin is! String || !_hostName.hasMatch(priceOrigin))) {
      throw bad;
    }
    if (reference is! String ||
        reference.runes.length > 200 ||
        !_isPlainText(reference) ||
        _looksLikeUrl(reference, bareDomains: false)) {
      throw const _Reject('bad_reference');
    }
    final fetched = fetchedAt is String ? parseMockRfc3339Utc(fetchedAt) : null;
    if (fetched == null) throw const _Reject('bad_fetched_at');
    const skew = Duration(seconds: 5);
    if (fetched.isBefore(runStart.subtract(skew)) ||
        fetched.isAfter(now.add(skew))) {
      throw const _Reject('fetched_at_outside_run');
    }
  }
}

final RegExp _sourceKey = RegExp(r'^[A-Za-z0-9:._/-]{1,256}$');
final RegExp _hostName = RegExp(
  r'^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
  r'(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$',
);
final RegExp _decimal = RegExp(r'^(0|[1-9][0-9]*)(\.[0-9]+)?$');
final RegExp _rfc3339 = RegExp(
  r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$',
);

bool _validDecimal(Object? raw) {
  if (raw is! String || raw.length > 100 || !_decimal.hasMatch(raw)) {
    return false;
  }
  final point = raw.indexOf('.');
  final integer = point < 0 ? raw : raw.substring(0, point);
  final fraction = (point < 0 ? '' : raw.substring(point + 1)).replaceFirst(
    RegExp(r'0+$'),
    '',
  );
  if (fraction.length > 18) return false;
  final significant = integer != '0'
      ? integer.length + fraction.length
      : fraction.replaceFirst(RegExp(r'^0+'), '').length;
  return significant <= 38;
}

/// The canonical spelling of a valid decimal string: no trailing fractional
/// zeros (`0.51230000` is `0.5123`, `5.0` is `5`).
String canonicalMockDecimal(String text) {
  if (!text.contains('.')) return text;
  final trimmed = text.replaceFirst(RegExp(r'0+$'), '');
  return trimmed.endsWith('.')
      ? trimmed.substring(0, trimmed.length - 1)
      : trimmed;
}

/// RFC 3339 in UTC (`Z`, upper case, a `T`, optional fraction), or null.
DateTime? parseMockRfc3339Utc(String raw) {
  if (raw.length > 40) return null;
  final m = _rfc3339.firstMatch(raw);
  if (m == null) return null;
  final year = int.parse(m[1]!);
  final month = int.parse(m[2]!);
  final day = int.parse(m[3]!);
  final hour = int.parse(m[4]!);
  final minute = int.parse(m[5]!);
  final second = int.parse(m[6]!);
  if (year < 1 ||
      month < 1 ||
      month > 12 ||
      day < 1 ||
      hour > 23 ||
      minute > 59 ||
      second > 59) {
    return null;
  }
  final micros = int.parse((m[7] ?? '').padRight(6, '0').substring(0, 6));
  final parsed = DateTime.utc(
    year,
    month,
    day,
    hour,
    minute,
    second,
    micros ~/ 1000,
    micros % 1000,
  );
  if (parsed.month != month || parsed.day != day) return null;
  return parsed;
}

bool _isPlainText(String s) {
  for (final c in s.runes) {
    if (c >= 0xD800 && c <= 0xDFFF) return false;
    if (_forbidden(c)) return false;
  }
  return true;
}

bool _forbidden(int c) =>
    c < 0x20 ||
    (c >= 0x7F && c <= 0x9F) ||
    c == 0x00A0 ||
    c == 0x00AD ||
    c == 0x034F ||
    c == 0x061C ||
    c == 0x1680 ||
    c == 0x180E ||
    (c >= 0x2000 && c <= 0x200F) ||
    (c >= 0x2028 && c <= 0x202F) ||
    (c >= 0x205F && c <= 0x206F) ||
    c == 0x3000 ||
    c == 0xFEFF ||
    (c >= 0xE000 && c <= 0xF8FF) ||
    (c >= 0xFFF9 && c <= 0xFFFC) ||
    c == 0xFFFE ||
    c == 0xFFFF ||
    (c >= 0x1D173 && c <= 0x1D17A) ||
    (c >= 0xE0000 && c <= 0xE007F) ||
    c >= 0xF0000;

final RegExp _urlScheme = RegExp(
  r'[a-z][a-z0-9+.\-]{1,20}://',
  caseSensitive: false,
);
final RegExp _www = RegExp(r'(^|[^a-z0-9])www\.', caseSensitive: false);
final RegExp _linkScheme = RegExp(
  r'(^|[^a-z0-9])(mailto|javascript|vbscript|data|file|tel|sms|blob|http|https|ftp|ws|wss):',
  caseSensitive: false,
);
final RegExp _bareDomain = RegExp(
  r'(^|[^a-z0-9.-])[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*'
  r'\.(com|net|org|io|co|app|dev|xyz|info|biz|me|ly|to|cc|gg|sh|ai|tv|top|'
  r'site|online|link|click|club|shop|store|tech|cloud|finance|money|wallet|'
  r'crypto|eth|sol|uk|de|fr|jp|cn|ru|sg|hk|tk|ml|ga|cf|gq)'
  r'(?![a-z0-9-])',
  caseSensitive: false,
);

bool _looksLikeUrl(String s, {required bool bareDomains}) =>
    _urlScheme.hasMatch(s) ||
    _www.hasMatch(s) ||
    _linkScheme.hasMatch(s) ||
    (bareDomains && _bareDomain.hasMatch(s));

class _Shape {
  const _Shape({
    this.bytes = 0,
    this.hostField = false,
    this.malformed = false,
  });

  final int bytes;
  final bool hostField;
  final bool malformed;
}

/// Depth, size, non-JSON leaves and host-owned names, iteratively.
_Shape _scan(Object? root) {
  const maxDepth = 12;
  var bytes = 0;
  var hostField = false;
  final stack = <(Object?, int)>[(root, 0)];
  while (stack.isNotEmpty) {
    final (node, depth) = stack.removeLast();
    if (bytes > _maxPayloadBytes) break;
    if (node == null) {
      bytes += 4;
    } else if (node is bool) {
      bytes += node ? 4 : 5;
    } else if (node is num) {
      if (node is double && !node.isFinite)
        return const _Shape(malformed: true);
      bytes += node.toString().length;
    } else if (node is String) {
      bytes += utf8.encode(node).length + 2;
    } else if (node is List) {
      if (depth >= maxDepth) return const _Shape(malformed: true);
      bytes += 2 + (node.isEmpty ? 0 : node.length - 1);
      for (final child in node) {
        stack.add((child, depth + 1));
      }
    } else if (node is Map) {
      if (depth >= maxDepth) return const _Shape(malformed: true);
      bytes += 2 + (node.isEmpty ? 0 : node.length - 1);
      for (final entry in node.entries) {
        final key = entry.key;
        if (key is! String) return const _Shape(malformed: true);
        if (_hostOwnedFields.contains(key)) hostField = true;
        bytes += utf8.encode(key).length + 3;
        stack.add((entry.value, depth + 1));
      }
    } else {
      return const _Shape(malformed: true);
    }
  }
  return _Shape(bytes: bytes, hostField: hostField);
}

class _Checked {
  _Checked({this.batchReason, this.proposalReasons = const {}});

  final String? batchReason;
  final Map<int, String> proposalReasons;
}

class _Reject implements Exception {
  const _Reject(this.reason);

  final String reason;
}
