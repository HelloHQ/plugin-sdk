import 'dart:convert';

import 'package:hellohq_plugin_mock_host/mock_host.dart';
import 'package:test/test.dart';

MockHost _host({
  Iterable<String> granted = const [],
  MockGate? gate,
}) => MockHost(
  gate: gate,
  granted: granted,
  portfolios: const [
    MockPortfolio(
      'ptf_a',
      'Alpha',
      sheets: [
        MockSheet(
          'sh_a',
          'Assets',
          'asset',
          sections: [
            MockSection('sec_a', 'Cash', items: [
              MockItem('it1', 'Brokerage'),
              MockItem('it2', 'Savings'),
            ]),
          ],
        ),
        MockSheet(
          'sh_d',
          'Debts',
          'debt',
          sections: [
            MockSection('sec_d', 'Loans', items: [MockItem('it3', 'Mortgage')]),
          ],
        ),
      ],
      totals: {'usd': 330.0},
    ),
    MockPortfolio('ptf_b', 'Beta'),
  ],
  currencies: const [
    MockCurrency('usd', 'US Dollar', r'$', 1),
    MockCurrency('eur', 'Euro', '€', 2),
  ],
);

Map<String, dynamic> _resolve(MockHost h, String method, {String? portfolioId}) {
  final req = {'method': method, if (portfolioId != null) 'portfolio_id': portfolioId};
  return jsonDecode(h.resolve(jsonEncode(req))) as Map<String, dynamic>;
}

void main() {
  group('MockHost.resolve — gate semantics', () {
    test('granted read returns data', () {
      final r = _resolve(_host(granted: ['read:portfolio_names']), 'read:portfolio_names');
      expect(r['ok'], isTrue);
      expect((r['data'] as List).map((e) => e['name']), containsAll(['Alpha', 'Beta']));
    });

    test('ungranted read is denied', () {
      final r = _resolve(_host(), 'read:portfolio_names');
      expect(r['ok'], isFalse);
      expect(r['error'], 'denied:read:portfolio_names');
    });

    test('deny beats grant', () {
      final gate = MockGate(
        granted: [{'id': 'read:portfolio_names'}],
        denied: {'read:portfolio_names'},
      );
      final r = _resolve(_host(gate: gate), 'read:portfolio_names');
      expect(r['ok'], isFalse);
    });

    test('unknown method is rejected', () {
      final r = _resolve(_host(granted: ['read:portfolio_names']), 'read:nope');
      expect(r['error'], startsWith('unknown_method'));
    });
  });

  group('scope filtering', () {
    test('scoped grant narrows enumeration', () {
      final gate = MockGate(granted: [
        {'id': 'read:portfolio_names', 'scope': {'portfolios': ['ptf_a']}},
      ]);
      final r = _resolve(_host(gate: gate), 'read:portfolio_names');
      expect((r['data'] as List).map((e) => e['id']), ['ptf_a']);
    });

    test('targeted read outside scope is denied', () {
      final gate = MockGate(granted: [
        {'id': 'read:sheet_structure', 'scope': {'portfolios': ['ptf_a']}},
      ]);
      final r = _resolve(_host(gate: gate), 'read:sheet_structure', portfolioId: 'ptf_b');
      expect(r['ok'], isFalse);
    });
  });

  group('data shapes (mirror PluginSyncReader)', () {
    test('sheet structure exposes names, not values', () {
      final r = _resolve(_host(granted: ['read:sheet_structure']), 'read:sheet_structure',
          portfolioId: 'ptf_a');
      final portfolios = (r['data'] as Map)['portfolios'] as List;
      final names = (portfolios.first as Map)['sheets']
          .expand((s) => (s as Map)['sections'] as List)
          .expand((sec) => (sec as Map)['items'] as List)
          .map((i) => (i as Map)['name']);
      expect(names, containsAll(['Brokerage', 'Savings', 'Mortgage']));
    });

    test('asset count splits asset/debt', () {
      final r = _resolve(_host(granted: ['read:asset_count']), 'read:asset_count',
          portfolioId: 'ptf_a');
      final p = ((r['data'] as Map)['portfolios'] as List).first as Map;
      expect(p['asset_items'], 2);
      expect(p['debt_items'], 1);
      expect(p['total_items'], 3);
    });

    test('currency rates list shape', () {
      final r = _resolve(_host(granted: ['read:currency_rates']), 'read:currency_rates');
      final usd = (r['data'] as List).firstWhere((c) => c['id'] == 'usd') as Map;
      expect(usd['symbol'], r'$');
      expect(usd['rate'], 1);
    });

    test('aggregated values per currency', () {
      final r = _resolve(_host(granted: ['read:aggregated_values']), 'read:aggregated_values',
          portfolioId: 'ptf_a');
      final totals = (((r['data'] as Map)['portfolios'] as List).first as Map)['totals'] as List;
      expect((totals.first as Map)['currency_id'], 'usd');
      expect((totals.first as Map)['total'], 330.0);
    });
  });

  test('emit captures events', () {
    final h = _host();
    h.emit('shares-ready', '{"x":1}');
    expect(h.emittedEvents, hasLength(1));
    expect(h.emittedEvents.first.name, 'shares-ready');
  });

  group('MockSidecarHost http_request', () {
    Map<String, dynamic> fetch(
      MockNetworkCallback cb, {
      Map<String, dynamic> headers = const {},
    }) =>
        jsonDecode(MockSidecarHost(granted: ['network:fetch'], onNetworkFetch: cb)
            .handleLine(jsonEncode({
          'type': 'http_request',
          'seq': 7,
          'method': 'GET',
          'url': 'https://api.example.com/x',
          'headers': headers,
        }))!) as Map<String, dynamic>;

    test('a UTF-8 body is sent as text with no body_encoding', () {
      final r = fetch((_) => {'status': 200, 'body': utf8.encode('Zoë €1')});
      expect(r['seq'], 7);
      expect(r['body'], 'Zoë €1');
      expect(r.containsKey('body_encoding'), isFalse);
    });

    test('a String body is passed through unchanged', () {
      final r = fetch((_) => {'status': 200, 'body': '{"ok":true}'});
      expect(r['body'], '{"ok":true}');
      expect(r.containsKey('body_encoding'), isFalse);
    });

    test('non-UTF-8 bytes are sent base64 with body_encoding', () {
      final bytes = [for (var i = 0; i < 256; i++) i];
      final r = fetch((_) => {'status': 200, 'body': bytes});
      expect(r['body_encoding'], 'base64');
      expect(base64Decode(r['body'] as String), bytes);
    });

    test('an explicit body_encoding on a String body is passed through', () {
      final r = fetch((_) => {'status': 200, 'body': 'AAE=', 'body_encoding': 'base64'});
      expect(r['body'], 'AAE=');
      expect(r['body_encoding'], 'base64');
    });

    test('only allowlisted request headers reach the callback', () {
      Map<String, dynamic>? seen;
      fetch(
        (req) {
          seen = req;
          return {'status': 200, 'body': ''};
        },
        headers: {
          'Accept': 'application/json',
          'If-None-Match': '"v1"',
          'Authorization': 'Bearer t',
          'User-Agent': 'x',
          'X-API-Key': 'k',
        },
      );
      expect(seen!['headers'], {'accept': 'application/json', 'if-none-match': '"v1"'});
    });

    test('Set-Cookie is stripped from the response', () {
      final r = fetch((_) => {
            'status': 200,
            'headers': {'Set-Cookie': 'a=b', 'content-type': 'text/plain'},
            'body': '',
          });
      expect(r['headers'], {'content-type': 'text/plain'});
    });

    test('denied without network:fetch', () {
      final r = jsonDecode(MockSidecarHost().handleLine(jsonEncode(
              {'type': 'http_request', 'seq': 1, 'method': 'GET', 'url': 'https://x'}))!)
          as Map<String, dynamic>;
      expect(r['error_code'], 'permission_denied');
    });
  });
}
