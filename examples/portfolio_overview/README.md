# Portfolio Overview — reference WebView example

The **reference example** for a HelloHQ Tier-2 plugin with a framework-agnostic
WebView UI. It lists the portfolios in your workspace.

It has two halves, the way every WebView plugin does:

| Half | Lives in | Built to | Role |
|------|----------|----------|------|
| **Compute** | `src/lib.rs` | `portfolio_overview.component.wasm` | A `hellohq:plugin@0.1.0` component (Rust SDK). Reads portfolio names (permission-gated) and returns JSON. |
| **UI** | `ui/` | `ui.zip` | A static web bundle (here: vanilla TypeScript). Rendered by HelloHQ in a WebView; talks to the host via `window.HQBridge`. |

The manifest (`manifest.json`) declares both: `wasm_url` (the component) and
`ui_bundle_url` (the UI), with `ui_type: "webview"`.

## How the two halves talk

HelloHQ serves the UI bundle from a loopback origin under a strict CSP
(`script-src 'self'`) and injects `window.HQBridge`. The
[`@hellohq/plugin-sdk`](../../sdks/js) `HQHost` class wraps that bridge so the UI
never hand-rolls `postMessage`:

```ts
import { HQHost } from "@hellohq/plugin-sdk";
const host = new HQHost();

// Run the Wasm component (bridge `compute` action -> guest.run):
const data = await host.compute("overview", {});

// Or read host-mediated, permission-gated data directly (no Wasm):
const names = await host.readPortfolioNames();
```

- `host.compute(fn, args)` runs the plugin's component `run({function, args})`
  and resolves with the decoded JSON it returns.
- `host.read*()` are host-mediated, permission-gated reads — the WebView never
  touches the database or network directly.
- `host.on(event, cb)` receives push events the component emits with
  `hq::events::emit(...)` during a run (not used here; see `fx-advisor` /
  `portfolio-analyst` for streaming progress).

**This is framework-agnostic.** Swap the `ui/` bundle for Svelte, Vue, or React
and nothing else changes — the contract is `window.HQBridge` / `HQHost`. The
other examples build the same UX in different frameworks to prove it.

## Why split compute from UI?

The component holds all logic and capability use, so the granted-permission set
and the audited code stay small and reviewable; the UI is a pure presentation
layer that cannot reach data except through the gated bridge. Here the component
imports only `hellohq:plugin/{workspace,log}` (verify with
`wasm-tools component wit`) — `wasm-tools component new` tree-shakes the rest out.

## Build

```bash
./build.sh
```

This builds the component (`cargo` + `wasm-tools component new`), bundles the UI
(`esbuild` + the SDK), zips `ui/dist` into `ui.zip`, and prints the sha256 of
both artifacts to paste into `manifest.json` (`content_hash_sha256` /
`ui_bundle_hash_sha256`) at release time. The committed manifest carries
placeholder zero hashes.

Requirements: the `wasm32-unknown-unknown` Rust target, `wasm-tools`, and
Node + npm.

## Test locally

```bash
hqplugin test --wasm portfolio_overview.component.wasm --bundle ui.zip
```

opens a mock host (permission gate + fixture data) so you can exercise the UI
without the full app.
