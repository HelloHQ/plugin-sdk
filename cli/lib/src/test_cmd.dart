import 'dart:convert';
import 'dart:io';

import 'package:hellohq_plugin_mock_host/mock_host.dart';
import 'package:hqplugin/src/wasm/wasm_runner.dart';

/// A small built-in fixture so `hqplugin test --wasm x --grant read:...` works
/// without a `--fixture` file.
MockHost _demoHost(List<String> grants) => MockHost(
  granted: grants,
  portfolios: const [
    MockPortfolio(
      'ptf_personal',
      'Personal',
      sheets: [
        MockSheet('sh1', 'Investments', 'asset', sections: [
          MockSection('sec1', 'Brokerage', items: [
            MockItem('it1', 'Index Fund'),
            MockItem('it2', 'Tech Stocks'),
          ]),
        ]),
      ],
      totals: {'usd': 125000.0},
    ),
    MockPortfolio('ptf_business', 'Business', totals: {'usd': 48000.0}),
  ],
  currencies: const [
    MockCurrency('usd', 'US Dollar', r'$', 1),
    MockCurrency('eur', 'Euro', '€', 2),
  ],
);

/// Run a Tier-2 plugin against the mock host and print its declarative output.
Future<int> runTest({
  String? wasmPath,
  List<String> grants = const [],
  String? fixturePath,
  String input = '{"function":"main","args":{}}',
  StringSink? out_,
  StringSink? err_,
}) async {
  final o = out_ ?? stdout;
  final e = err_ ?? stderr;

  if (wasmPath == null) {
    e.writeln('test: --wasm is required (Tier-2 plugin).');
    return 64;
  }
  final file = File(wasmPath);
  if (!file.existsSync()) {
    e.writeln('test: no such file: $wasmPath');
    return 66;
  }
  if (!WasmRunner.isRuntimeAvailable) {
    e.writeln(
      'test: the Wasm runtime is unavailable. Run scripts/fetch-wasmtime-libs.sh '
      'in the plugin-sdk repo, or set HQPLUGIN_WASMTIME_LIB.',
    );
    return 69;
  }

  final MockHost host;
  try {
    host = fixturePath == null
        ? _demoHost(grants)
        : _hostFromFixture(fixturePath, grants);
  } on FormatException catch (ex) {
    e.writeln('test: bad --fixture: ${ex.message}');
    return 65;
  }

  o.writeln('test: ${file.path}  grants=${grants.isEmpty ? "(none)" : grants.join(",")}');
  final runner = WasmRunner(resolver: host.resolve, onEvent: host.emit);
  try {
    runner.load(file.readAsBytesSync());
    final outputJson = runner.runBytes(input);

    final Object? decoded;
    try {
      decoded = jsonDecode(outputJson);
    } on FormatException {
      e.writeln('test: plugin returned non-JSON output:\n$outputJson');
      return 70;
    }
    o.writeln('\n── Declarative output ──');
    o.writeln(const JsonEncoder.withIndent('  ').convert(decoded));
    if (host.emittedEvents.isNotEmpty) {
      o.writeln('\n── Emitted events ──');
      for (final ev in host.emittedEvents) {
        o.writeln('  ${ev.name}: ${ev.payload}');
      }
    }
    return 0;
  } on WasmRunError catch (ex) {
    e.writeln('test: plugin failed to run — ${ex.message}');
    return 70;
  } finally {
    runner.dispose();
  }
}

/// Parse a `--fixture` JSON file into a [MockHost]. Schema:
/// ```json
/// {
///   "portfolios": [
///     {"id": "ptf_a", "name": "Alpha", "totals": {"usd": 1000},
///      "sheets": [{"id":"s","name":"S","type":"asset",
///                  "sections":[{"id":"sec","name":"Sec",
///                               "items":[{"id":"it","name":"Cash"}]}]}]}
///   ],
///   "currencies": [{"id":"usd","name":"US Dollar","symbol":"$","rate":1}]
/// }
/// ```
MockHost _hostFromFixture(String path, List<String> grants) {
  final raw = jsonDecode(File(path).readAsStringSync());
  if (raw is! Map<String, dynamic>) {
    throw const FormatException('top level must be an object');
  }
  final portfolios = [
    for (final pf in (raw['portfolios'] as List? ?? const []))
      _portfolio(pf as Map<String, dynamic>),
  ];
  final currencies = [
    for (final c in (raw['currencies'] as List? ?? const []))
      MockCurrency(
        c['id'] as String,
        (c['name'] ?? '') as String,
        (c['symbol'] ?? '') as String,
        (c['rate'] ?? 0) as num,
      ),
  ];
  return MockHost(granted: grants, portfolios: portfolios, currencies: currencies);
}

MockPortfolio _portfolio(Map<String, dynamic> p) => MockPortfolio(
  p['id'] as String,
  (p['name'] ?? '') as String,
  totals: {
    for (final e in (p['totals'] as Map<String, dynamic>? ?? const {}).entries)
      e.key: (e.value as num).toDouble(),
  },
  sheets: [
    for (final s in (p['sheets'] as List? ?? const []))
      MockSheet(
        s['id'] as String,
        (s['name'] ?? '') as String,
        (s['type'] ?? 'asset') as String,
        sections: [
          for (final sec in (s['sections'] as List? ?? const []))
            MockSection(
              sec['id'] as String,
              (sec['name'] ?? '') as String,
              items: [
                for (final it in (sec['items'] as List? ?? const []))
                  MockItem(it['id'] as String, (it['name'] ?? '') as String),
              ],
            ),
        ],
      ),
  ],
);
