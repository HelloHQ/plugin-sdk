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
}
