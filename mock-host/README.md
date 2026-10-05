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

## Tier 1 `propose` (a mock)

`MockSidecarHost` answers the Tier 1 NDJSON `propose` message so
`hqplugin test --sidecar` can exercise a plugin that proposes holdings and
values. It accepts a valid batch and returns one receipt per proposal
(`queued`, `duplicate`, `superseded_older`, or `invalid` with a reason code),
and refuses what the real host refuses (`permission_denied`, `bad_request`,
`too_many`, `too_large`, `rate_limit_exceeded`, `quota_exceeded`) using the same
closed code set as `plugin-protocol`'s `host-calls.schema.json`.

```dart
final host = MockSidecarHost(
  // A propose grant needs its kinds, as the manifest's scope.kinds does.
  granted: ['propose:holdings=crypto_ticker,home', 'propose:valuations=home'],
);
// ... drive the plugin's stdout lines through host.handleLine(...) ...
host.proposer.queued; // every proposal accepted this run, in memory only
```

It is a mock: nothing is saved, there is no review UI, binding or rejection
memory, and it approximates the host's validator (it does not know the host's
currency lists and does not check that `source.origin` was fetched this run).
`unchanged` and `suppressed` depend on the person's data, so force them with
`MockProposer(forcedOutcomes: {'<source_key>': 'unchanged'})`. The real host
wins any disagreement.

