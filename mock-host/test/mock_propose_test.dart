import 'dart:convert';

import 'package:hellohq_plugin_mock_host/mock_host.dart';
import 'package:test/test.dart';

final _now = DateTime.utc(2026, 10, 4, 6, 0, 30);

Map<String, dynamic> holding([Map<String, dynamic> over = const {}]) => {
  'kind': 'holding',
  'source_key': 'btc:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
  'asset_kind': 'crypto_ticker',
  'display_name': 'Cold wallet (bc1qxy...0wlh)',
  'instrument': {'symbol': 'BTC', 'chain': 'bitcoin'},
  'quantity': {'amount': '0.5123', 'unit': 'BTC'},
  'value': {'amount': '31744.12', 'currency': 'USD'},
  'as_of': '2026-10-04T06:00:00Z',
  'method': 'quoted_price',
  'source': {
    'origin': 'mempool.space',
    'reference': '/api/address/bc1qxy.../utxo',
    'fetched_at': '2026-10-04T06:00:02Z',
    'price_origin': 'api.coingecko.com',
  },
  ...over,
};

Map<String, dynamic> valuation([Map<String, dynamic> over = const {}]) => {
  'kind': 'valuation',
  'source_key': 'uk-lr:uprn:100023336956',
  'value': {'amount': '412500', 'currency': 'GBP'},
  'as_of': '2026-09-30T00:00:00Z',
  'method': 'comparable_sales_median',
  'source': {
    'origin': 'landregistry.data.gov.uk',
    'reference': 'price-paid: 14 sales within 400 m',
    'fetched_at': '2026-10-04T06:00:10Z',
  },
  ...over,
};

Map<String, dynamic> batch(List<Map<String, dynamic>> proposals) => {
  'schema': 'hellohq.proposal-batch@1',
  'proposals': proposals,
};

MockSidecarHost _host({
  Iterable<String> grants = const [
    'propose:holdings=crypto_ticker',
    'propose:valuations=home',
  ],
  MockProposer? proposer,
}) => MockSidecarHost(
  granted: grants,
  proposer:
      proposer ??
      MockProposer(
        clock: () => _now,
        runStart: DateTime.utc(2026, 10, 4, 6, 0, 0),
      ),
);

Map<String, dynamic> _propose(
  MockSidecarHost host,
  Object? batchValue, {
  int seq = 3,
}) =>
    jsonDecode(
          host.handleLine(
            jsonEncode({'type': 'propose', 'seq': seq, 'batch': batchValue}),
          )!,
        )
        as Map<String, dynamic>;

/// The one receipt of a single-proposal batch.
Map<String, dynamic> _one(Map<String, dynamic> proposal, {MockSidecarHost? h}) {
  final r = _propose(h ?? _host(), batch([proposal]));
  expect(r['type'], 'propose_response');
  expect(r.containsKey('error'), isFalse, reason: '$r');
  return (r['receipts'] as List).single as Map<String, dynamic>;
}

Map<String, dynamic> _with(
  Map<String, dynamic> base,
  String key,
  Object? value,
) => {...base, key: value};

Map<String, dynamic> _deep(
  Map<String, dynamic> base,
  String outer,
  String key,
  Object? value,
) => {
  ...base,
  outer: {...(base[outer] as Map<String, dynamic>), key: value},
};

void main() {
  group('MockSidecarHost propose: accepted', () {
    test('a valid batch is queued with one receipt per proposal, in order', () {
      final h = _host();
      final r = _propose(h, batch([holding(), valuation()]), seq: 12);
      expect(r['type'], 'propose_response');
      expect(r['seq'], 12);
      expect(r['receipts'], [
        {'index': 0, 'outcome': 'queued'},
        {'index': 1, 'outcome': 'queued'},
      ]);
      expect(h.proposer.queued, hasLength(2));
    });

    test('a receipt carries only index, outcome and (invalid) reason', () {
      final r = _propose(
        _host(),
        batch([
          holding(),
          valuation({'method': 'x'}),
        ]),
      );
      for (final receipt in r['receipts'] as List) {
        expect(
          (receipt as Map).keys,
          everyElement(anyOf('index', 'outcome', 'reason')),
        );
      }
    });

    test('an empty batch is accepted with no receipts', () {
      expect(_propose(_host(), batch([]))['receipts'], isEmpty);
    });

    test('null holding-only fields on a valuation are tolerated', () {
      expect(
        _one(valuation({'asset_kind': null, 'display_name': null}))['outcome'],
        'queued',
      );
    });
  });

  group('MockSidecarHost propose: per-proposal reasons', () {
    final cases = <String, (Map<String, dynamic>, String)>{
      'bad_kind': (_with(holding(), 'kind', 'transaction'), 'bad_kind'),
      'unknown_field': (_with(holding(), 'note', 'x'), 'unknown_field'),
      'field_not_allowed_for_kind': (
        _with(valuation(), 'asset_kind', 'home'),
        'field_not_allowed_for_kind',
      ),
      'missing source_key': (
        {...holding()}..remove('source_key'),
        'missing_field',
      ),
      'bad_source_key': (
        _with(holding(), 'source_key', 'a b'),
        'bad_source_key',
      ),
      'missing asset_kind': (
        {...holding()}..remove('asset_kind'),
        'missing_field',
      ),
      'bad_asset_kind': (
        _with(holding(), 'asset_kind', 'yacht'),
        'bad_asset_kind',
      ),
      'asset_kind_not_allowed': (
        _with(holding(), 'asset_kind', 'home'),
        'asset_kind_not_allowed',
      ),
      'bad_display_name url': (
        _with(holding(), 'display_name', 'see https://x.example'),
        'bad_display_name',
      ),
      'bad_display_name bare domain': (
        _with(holding(), 'display_name', 'Wallet example.com'),
        'bad_display_name',
      ),
      'bad_display_name newline': (
        _with(holding(), 'display_name', 'a\nb'),
        'bad_display_name',
      ),
      'bad_display_name padded': (
        _with(holding(), 'display_name', ' x'),
        'bad_display_name',
      ),
      'bad_display_name 81 chars': (
        _with(holding(), 'display_name', 'x' * 81),
        'bad_display_name',
      ),
      'bad_instrument': (
        _with(holding(), 'instrument', {'isin': 'x'}),
        'bad_instrument',
      ),
      'bad_quantity float': (
        _deep(holding(), 'quantity', 'amount', 0.5),
        'bad_quantity',
      ),
      'bad_quantity leading zero': (
        _deep(holding(), 'quantity', 'amount', '01'),
        'bad_quantity',
      ),
      'bad_quantity scale 19': (
        _deep(holding(), 'quantity', 'amount', '0.${'1' * 19}'),
        'bad_quantity',
      ),
      'bad_quantity 39 digits': (
        _deep(holding(), 'quantity', 'amount', '1' * 39),
        'bad_quantity',
      ),
      'bad_quantity unit': (
        _deep(holding(), 'quantity', 'unit', 'mint:abc'),
        'bad_quantity',
      ),
      'bad_value negative': (
        _deep(holding(), 'value', 'amount', '-1'),
        'bad_value',
      ),
      'bad_currency': (
        _deep(holding(), 'value', 'currency', 'usd'),
        'bad_currency',
      ),
      'valuation without value': (
        {...valuation()}..remove('value'),
        'missing_field',
      ),
      'missing as_of': ({...holding()}..remove('as_of'), 'missing_field'),
      'bad_as_of date only': (
        _with(holding(), 'as_of', '2026-10-04'),
        'bad_as_of',
      ),
      'bad_as_of impossible date': (
        _with(holding(), 'as_of', '2026-02-30T00:00:00Z'),
        'bad_as_of',
      ),
      'as_of_in_future': (
        _with(holding(), 'as_of', '2026-10-04T06:06:00Z'),
        'as_of_in_future',
      ),
      'as_of_too_old': (
        _with(holding(), 'as_of', '2016-10-03T00:00:00Z'),
        'as_of_too_old',
      ),
      'bad_method': (_with(holding(), 'method', 'vibes'), 'bad_method'),
      'missing source': ({...holding()}..remove('source'), 'missing_field'),
      'bad_source extra key': (
        _deep(holding(), 'source', 'url', 'x'),
        'bad_source',
      ),
      'bad_source origin': (
        _deep(holding(), 'source', 'origin', 'https://a.example'),
        'bad_source',
      ),
      'bad_reference url': (
        _deep(holding(), 'source', 'reference', 'see https://a.example'),
        'bad_reference',
      ),
      'bad_fetched_at': (
        _deep(holding(), 'source', 'fetched_at', '2026-10-04'),
        'bad_fetched_at',
      ),
      'fetched_at_outside_run before': (
        _deep(holding(), 'source', 'fetched_at', '2026-10-04T05:00:00Z'),
        'fetched_at_outside_run',
      ),
      'fetched_at_outside_run after': (
        _deep(holding(), 'source', 'fetched_at', '2026-10-04T06:01:00Z'),
        'fetched_at_outside_run',
      ),
    };
    cases.forEach((name, c) {
      test(name, () {
        final receipt = _one(c.$1);
        expect(receipt['outcome'], 'invalid');
        expect(receipt['reason'], c.$2);
        expect(mockProposeReasons, contains(c.$2));
      });
    });

    test('one invalid proposal does not stop the others', () {
      final h = _host();
      final r = _propose(
        h,
        batch([
          valuation(),
          valuation({'method': 'x'}),
        ]),
      );
      expect((r['receipts'] as List).map((e) => e['outcome']), [
        'queued',
        'invalid',
      ]);
      expect(h.proposer.queued, hasLength(1));
    });

    test('a reference may cite a host name; a display name may not', () {
      expect(
        _one(
          _deep(
            valuation(),
            'source',
            'reference',
            'landregistry.data.gov.uk: 14 sales',
          ),
        )['outcome'],
        'queued',
      );
    });

    test(
      'amounts are canonicalised: trailing zeros do not change identity',
      () {
        final h = _host();
        expect(_one(valuation(), h: h)['outcome'], 'queued');
        final same = _deep(valuation(), 'value', 'amount', '412500.000');
        expect(_one(same, h: h)['outcome'], 'duplicate');
      },
    );
  });

  group('MockSidecarHost propose: whole-call refusals', () {
    Map<String, dynamic> refused(Object? b, {MockSidecarHost? h}) {
      final r = _propose(h ?? _host(), b);
      expect(r.containsKey('receipts'), isFalse);
      return r;
    }

    test('permission_denied without any propose grant', () {
      final r = refused(batch([valuation()]), h: _host(grants: const []));
      expect(r['error_code'], 'permission_denied');
      expect(r['error'], 'propose permission is not granted for this plugin.');
    });

    test('a propose grant without scope.kinds is not a grant', () {
      final r = refused(
        batch([valuation()]),
        h: _host(grants: const ['propose:valuations']),
      );
      expect(r['error_code'], 'permission_denied');
    });

    test('a grant with an unknown kind is not a grant', () {
      final r = refused(
        batch([valuation()]),
        h: _host(grants: const ['propose:valuations=yacht']),
      );
      expect(r['error_code'], 'permission_denied');
    });

    test('a kind without its own permission is invalid per proposal', () {
      final h = _host(grants: const ['propose:valuations=home']);
      final r = _propose(h, batch([holding(), valuation()]));
      expect(r['receipts'], [
        {'index': 0, 'outcome': 'invalid', 'reason': 'permission_denied'},
        {'index': 1, 'outcome': 'queued'},
      ]);
    });

    test('the scope is per permission', () {
      final h = _host(
        grants: const ['propose:holdings=home', 'propose:valuations=home'],
      );
      // crypto_ticker is not in this holdings grant's scope.
      expect(_one(holding(), h: h)['reason'], 'asset_kind_not_allowed');
    });

    test('bad_request: not a batch / bad schema / extra field', () {
      expect(refused('x')['error_code'], 'bad_request');
      expect(
        refused({'schema': 'nope', 'proposals': []})['reason'],
        'bad_schema',
      );
      expect(
        refused({
          'schema': 'hellohq.proposal-batch@1',
          'proposals': {},
        })['reason'],
        'invalid',
      );
      expect(
        refused({
          'schema': 'hellohq.proposal-batch@1',
          'proposals': [],
          'x': 1,
        })['reason'],
        'unknown_field',
      );
    });

    test('a host-owned field anywhere refuses the whole batch', () {
      for (final name in [
        'plugin_id',
        'run_id',
        'dedup_key',
        'source_observed',
      ]) {
        final r = refused(batch([_deep(valuation(), 'source', name, 'x')]));
        expect(r['error_code'], 'bad_request', reason: name);
        expect(r['reason'], 'host_field_supplied', reason: name);
      }
    });

    test('too_many: more than 200 proposals or 50 holdings', () {
      final many = [
        for (var i = 0; i < 201; i++) valuation({'source_key': 'k:$i'}),
      ];
      final r = refused(batch(many));
      expect(r['error_code'], 'too_many');
      expect(r['reason'], 'too_many');
      final holdings = [
        for (var i = 0; i < 51; i++) holding({'source_key': 'k:$i'}),
      ];
      expect(refused(batch(holdings))['error_code'], 'too_many');
      final fifty = [
        for (var i = 0; i < 50; i++) holding({'source_key': 'k:$i'}),
      ];
      expect(_propose(_host(), batch(fifty))['receipts'], hasLength(50));
    });

    test('too_large: more than 256 KiB', () {
      final big = _deep(valuation(), 'source', 'reference', 'x' * 300000);
      final r = refused(batch([big]));
      expect(r['error_code'], 'too_large');
      expect(r['error'], 'The proposal batch is too large.');
    });

    test(
      'rate_limit_exceeded after 10 calls a minute; refused calls are free',
      () {
        var clock = _now;
        final h = _host(
          proposer: MockProposer(
            clock: () => clock,
            runStart: DateTime.utc(2026, 10, 4, 6, 0, 0),
          ),
        );
        for (var i = 0; i < 10; i++) {
          expect(_propose(h, batch([]))['error_code'], isNull);
        }
        expect(_propose(h, batch([]))['error_code'], 'rate_limit_exceeded');
        expect(_propose(h, batch([]))['error_code'], 'rate_limit_exceeded');
        clock = clock.add(const Duration(seconds: 61));
        expect(_propose(h, batch([]))['error_code'], isNull);
      },
    );

    test('rate_limit_exceeded after 100 calls a day', () {
      var clock = _now;
      final h = _host(
        proposer: MockProposer(
          clock: () => clock,
          runStart: DateTime.utc(2026, 10, 4, 6, 0, 0),
        ),
      );
      for (var i = 0; i < 100; i++) {
        clock = clock.add(const Duration(minutes: 2));
        expect(_propose(h, batch([]))['error_code'], isNull, reason: '$i');
      }
      clock = clock.add(const Duration(minutes: 2));
      expect(_propose(h, batch([]))['error_code'], 'rate_limit_exceeded');
    });

    test('quota_exceeded refuses a batch that would pass 500 pending', () {
      final h = _host();
      List<Map<String, dynamic>> run(int from) => [
        for (var i = from; i < from + 200; i++)
          valuation({'source_key': 'k:$i'}),
      ];
      expect(_propose(h, batch(run(0)))['receipts'], hasLength(200));
      expect(_propose(h, batch(run(200)))['receipts'], hasLength(200));
      final over = _propose(h, batch(run(400)));
      expect(over['error_code'], 'quota_exceeded');
      expect(
        h.proposer.queued,
        hasLength(400),
      ); // nothing from the refused call
      // A batch that queues nothing new never trips it.
      expect(
        _propose(h, batch(run(0).take(5).toList()))['receipts'],
        hasLength(5),
      );
    });
  });

  group('MockSidecarHost propose: outcomes', () {
    test('duplicate: the same proposal again', () {
      final h = _host();
      expect(_one(valuation(), h: h)['outcome'], 'queued');
      expect(_one(valuation(), h: h)['outcome'], 'duplicate');
      expect(h.proposer.queued, hasLength(1));
    });

    test('duplicate within one batch', () {
      final r = _propose(_host(), batch([valuation(), valuation()]));
      expect((r['receipts'] as List).map((e) => e['outcome']), [
        'queued',
        'duplicate',
      ]);
    });

    test('superseded_older: a new value for a key that is still pending', () {
      final h = _host();
      expect(_one(valuation(), h: h)['outcome'], 'queued');
      final newer = _deep(valuation(), 'value', 'amount', '420000');
      expect(_one(newer, h: h)['outcome'], 'superseded_older');
      // The superseded one no longer counts as the pending one.
      expect(_one(valuation(), h: h)['outcome'], 'superseded_older');
    });

    test('unchanged and suppressed can be forced by source_key', () {
      final proposer = MockProposer(
        clock: () => _now,
        runStart: DateTime.utc(2026, 10, 4, 6, 0, 0),
        forcedOutcomes: {
          'uk-lr:uprn:1': 'unchanged',
          'uk-lr:uprn:2': 'suppressed',
          'uk-lr:uprn:3': 'garbage',
        },
      );
      final h = _host(proposer: proposer);
      final r = _propose(
        h,
        batch([
          valuation({'source_key': 'uk-lr:uprn:1'}),
          valuation({'source_key': 'uk-lr:uprn:2'}),
          valuation({'source_key': 'uk-lr:uprn:3'}),
          valuation({'source_key': 'uk-lr:uprn:2', 'method': 'x'}),
        ]),
      );
      expect((r['receipts'] as List).map((e) => e['outcome']), [
        'unchanged',
        'suppressed',
        'queued', // only the four documented outcomes can be forced
        'invalid', // a forced outcome never overrides validation
      ]);
      expect(h.proposer.queued, hasLength(1));
    });
  });

  group('MockSidecarHost propose: envelope', () {
    test(
      'a response carries type, seq and exactly one of receipts or error',
      () {
        final h = _host();
        final ok = _propose(h, batch([valuation()]), seq: 41);
        expect(ok.keys.toSet(), {'type', 'seq', 'receipts'});
        final denied = _propose(_host(grants: const []), batch([]), seq: 42);
        expect(denied.keys.toSet(), {'type', 'seq', 'error', 'error_code'});
        expect(denied['seq'], 42);
      },
    );

    test('a bad_request carries its reason code', () {
      final r = _propose(_host(), {'schema': 'x', 'proposals': []});
      expect(r.keys.toSet(), {'type', 'seq', 'error', 'error_code', 'reason'});
    });

    test('propose without a seq gets no reply (like every host call)', () {
      expect(
        _host().handleLine(jsonEncode({'type': 'propose', 'batch': {}})),
        isNull,
      );
    });
  });

  group('MockGate.parse', () {
    test('propose ids take kinds after "="; others are plain ids', () {
      final g = MockGate.parse([
        'plugin:storage',
        'propose:holdings=crypto_ticker, home',
        'propose:valuations',
      ]);
      expect(g.isGranted('plugin:storage'), isTrue);
      expect(g.allowedKinds('propose:holdings'), {'crypto_ticker', 'home'});
      expect(g.allowedKinds('propose:valuations'), isNull);
    });

    test('deny beats grant', () {
      final g = MockGate(
        granted: [
          {
            'id': 'propose:holdings',
            'scope': {
              'kinds': ['home'],
            },
          },
        ],
        denied: {'propose:holdings'},
      );
      expect(g.allowedKinds('propose:holdings'), isNull);
    });
  });
}
