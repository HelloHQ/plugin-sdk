# Allocation Lens — AI narrative WebView example

A HelloHQ Tier-2 plugin that turns your portfolio structure into a plain-English
allocation narrative. It reads your portfolio names and per-category item counts,
then asks the host AI to describe what the allocation shape implies — **without
ever sending raw account values to the model**.

It has two halves, the way every WebView plugin does:

| Half | Lives in | Built to | Role |
|------|----------|----------|------|
| **Compute** | `src/lib.rs` | `allocation_lens.component.wasm` | A `hellohq:plugin@0.1.0` streaming-inference component (Rust, async `inference-quickstart` world). Takes a context string, calls `inference::complete`, drains the token stream, returns `{ "narrative": "…" }`. |
| **UI** | `ui/` | `ui.zip` | A static Svelte bundle. Rendered by HelloHQ in a WebView; reads workspace data and drives compute via `window.HQBridge`. |

The manifest (`manifest.json`) declares both: `wasm_url` (the component) and
`ui_bundle_url` (the UI), with `ui_type: "webview"`. WebView UIs run at the
**Verified** trust tier (the registry assigns the tier; the manifest does not
set it).

## How the two halves talk

HelloHQ serves the UI bundle from a loopback origin under a strict CSP
(`script-src 'self'`) and injects `window.HQBridge`. The
[`@hellohq/plugin-sdk`](../../sdks/js) `HQHost` class wraps that bridge so the UI
never hand-rolls `postMessage`:

```ts
import { HQHost } from "@hellohq/plugin-sdk";
const host = new HQHost();

// 1. Host-mediated, permission-gated reads (no Wasm):
const names = await host.readPortfolioNames();
const counts = await Promise.all(names.map((n) => host.readAssetCount(n.id)));

// 2. Build a compact context (names + item counts only — NO values) and run
//    the Wasm component (bridge `compute` action -> guest run):
const { narrative } = await host.compute<{ narrative: string }>("narrate", { context });
```

- `host.read*()` are host-mediated, permission-gated reads — the WebView never
  touches the database or network directly.
- `host.compute(fn, args)` runs the component `run({function, args})` and
  resolves with the decoded JSON it returns.

The UI assembles the context string and the component does the inference — so the
compute half imports only `hellohq:plugin/{types,log,inference}` (verify with
`wasm-tools component wit`); `wasm-tools component new` tree-shakes the rest out.

## Why split compute from UI (and reads from compute)?

There is no SDK world that exposes both `workspace` reads and `inference`, and
inference needs an async `run`. So the reads happen in the UI through the gated
bridge, and the component stays a focused, async streaming-inference unit. The
component holds the AI capability, keeping the granted-permission set and audited
code small; the UI is a pure presentation + orchestration layer that can reach
data only through the gated bridge. The model never sees raw values — only
portfolio names and per-category item counts.

## Build

```bash
./build.sh
```

This builds the component (`cargo` + `wasm-tools component new`), bundles the UI
(Vite + the SDK), zips `ui/dist` into `ui.zip`, and prints the sha256 of both
artifacts to paste into `manifest.json` (`content_hash_sha256` /
`ui_bundle_hash_sha256`) at release time. The committed manifest carries
placeholder zero hashes.

Requirements: the `wasm32-unknown-unknown` Rust target, `wasm-tools`, and
Node + npm.

## Test locally

```bash
hqplugin test --wasm allocation_lens.component.wasm --bundle ui.zip
```

opens a mock host (permission gate + fixture data + a stub inference backend) so
you can exercise the UI without the full app.
