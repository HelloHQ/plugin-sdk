# @hellohq/plugin-sdk (TypeScript / JavaScript)

Build Tier 2 HelloHQ plugins in TS/JS — either compiled to Wasm
(AssemblyScript / QuickJS-in-Wasm) or as a WebView UI.

## WebView UI

```ts
import { HQHost } from "@hellohq/plugin-sdk";

const host = new HQHost();
const portfolios = await host.readPortfolioNames();
const summary = await host.readAggregatedValues(portfolios[0].id);
const rows = await host.compute("compute_shares", { rows: [...] });

host.on("computation-complete", (payload) => updateChart(payload));
```

The WebView talks only to the host via the injected, validated `HQBridge` — it
cannot reach the network (CSP `connect-src 'none'`) or the Wasm binary directly.

> **Status:** typed surface is stable; transport wiring lands with the WebView
> host (Phase 6). Calls currently reject with a "not yet wired" error.

Protocol: https://github.com/HelloHQ/plugin-protocol
