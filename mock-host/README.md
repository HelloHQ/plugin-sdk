# Mock Host

An in-process mock of the HelloHQ host ABI so plugin authors can unit-test
host calls without running the app. Used by `hqplugin test`.

```dart
final host = MockHost(
  granted: {'read:portfolio_names'},
  portfolios: const [PortfolioName('ptf_a', 'Personal')],
);
host.readPortfolioNames();                 // -> [Personal]
host.readCurrencyRate('USD', 'EUR');       // throws permission-denied
```

It mirrors the real host's permission-gate semantics (deny > grant > scope), so
a plugin that passes here fails the same way in production when it over-reaches.

> **Status:** skeleton (Phase 4 — begin). Portfolio names + currency rates wired;
> remaining host reads land incrementally.
