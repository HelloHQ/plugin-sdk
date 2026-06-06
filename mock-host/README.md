# Mock Host

An in-process mock of the HelloHQ host ABI so plugin authors can unit-test
host calls without running the app. Used by `hqplugin test`.

`MockHost.resolve` answers the **exact `hq_read` JSON protocol** the production
`PluginSyncBridge` serves — same request/response shapes, same data shapes, and
the same permission-gate semantics (deny > grant > portfolio scope). A plugin
that works against this mock works against the real host.

```dart
final host = MockHost(
  granted: const {'read:portfolio_names'},
  portfolios: const [
    MockPortfolio('ptf_a', 'Personal'),
    MockPortfolio('ptf_b', 'Business'),
  ],
  currencies: const [MockCurrency('usd', 'US Dollar', r'$', 1)],
);

// Drive it with the same requests a plugin sends through hq_read:
host.resolve('{"method":"read:portfolio_names"}');
//   -> {"ok":true,"data":[{"id":"ptf_a","name":"Personal"}, ...]}

host.resolve('{"method":"read:currency_rates"}');
//   -> {"ok":false,"error":"denied:read:currency_rates"}   // not granted
```

## What's covered

All five reads — `read:portfolio_names`, `read:sheet_structure`,
`read:asset_count`, `read:currency_rates`, `read:aggregated_values` — with
portfolio-scoped grants, plus captured `emit` events. Fixture data is seeded
with `MockPortfolio` / `MockSheet` / `MockSection` / `MockItem` /
`MockCurrency`.

```bash
dart pub get
dart test
```
