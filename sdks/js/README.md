# @hellohq/plugin-sdk (TypeScript / JavaScript)

Build Tier-2 HelloHQ plugins in TS/JS, two ways:

- **Component Model** (`@hellohq/plugin-sdk/component`) — a headless plugin
  compiled to a WebAssembly **component** against the canonical
  `hellohq:plugin@0.1.0` WIT with [`jco`](https://github.com/bytecodealliance/jco)
  + `componentize-js`. This is the JS analogue of the Rust SDK.
- **WebView UI** (`@hellohq/plugin-sdk`) — a typed `HQHost` wrapper over the
  host-injected `HQBridge` for plugin UIs.

Both share `PROTOCOL_VERSION = "0.1.0"`.

---

## Component Model plugins (`@hellohq/plugin-sdk/component`)

```ts
import { hq, definePlugin } from "@hellohq/plugin-sdk/component";

const enc = new TextEncoder();

export const guest = definePlugin({
  init() {
    hq.log.info("my-plugin starting");
  },
  run(_input) {
    const names = hq.workspace.readPortfolioNames();         // throws ApiError on denial
    hq.storage.set("count", new Uint8Array([names.length]));
    hq.events.emit("scanned", enc.encode("ok"));
    return enc.encode(`${names.length} portfolios`);
  },
  metadata() {
    return { id: "my-plugin", version: "0.1.0" };
  },
});
```

The export **must** be named `guest` (it matches `export guest;` in the WIT
world). A complete, component-verified example is in
[`examples/component-quickstart-js`](../../examples/component-quickstart-js).

### API surface

All under the `hq` namespace — each function maps 1:1 onto a `hellohq:plugin/*`
import. Gated calls **return the value or throw an `ApiError`** (jco maps the WIT
`result<T, api-error>` to "return `T` / throw `api-error`"); this mirrors the
Rust SDK's `Result<…, ApiError>` in JS-idiomatic form.

| Namespace | Functions |
|---|---|
| `hq.workspace` | `readPortfolioNames`, `readSheetStructure`, `readAssetCount`, `readCurrencyRates`, `readAggregatedValues` |
| `hq.storage` | `get`, `set`, `delete`, `clear`, `listKeys` |
| `hq.events` | `emit(kind, payload)`, `emitEvent(event)` |
| `hq.log` | `trace`, `debug`, `info`, `warn`, `error`, `write(level, msg)` |
| `hq.inference` | `complete(messages, opts)` → `Promise<ReadableStream<string>>` (streaming), `collect(stream)`, message builders `user`/`system`/`assistant` — **see the inference caveat below** |

Plus:

- **`definePlugin({ init?, run, metadata })`** — defines the `guest` exports
  (`init` defaults to a no-op). The JS analogue of the Rust `Plugin` trait +
  `export_plugin!`.
- Clean re-exports of the generated records/errors: `ApiError`,
  `PortfolioName`, `CurrencyRate`, `SheetSummary`, `SheetInfo`, `AssetCount`,
  `CategoryCount`, `AggregatedSummary`, `CategoryTotal`, `PluginEvent`,
  `ChatMessage`, `InferenceOpts`, `PluginMetadata`, `LogLevel` — plus
  `isApiError(e)` to narrow a caught value.

The generated raw bindings live in `src/generated/` (escape hatch); regenerate
with `npm run gen:types`.

### Build workflow

jco's StarlingMonkey loader resolves only the single entry module plus the
`hellohq:*` / `wasi:*` virtual imports, so you **bundle to one ESM file first**
(esbuild/rollup), then componentize:

```sh
# 1. bundle, keeping the host virtuals external
npx esbuild src/plugin.ts --bundle --format=esm --platform=neutral \
  --external:'hellohq:*' --external:'wasi:*' --outfile=dist/plugin.bundle.js

# 2. componentize against the vendored WIT (build world omits `inference` — see below)
npx jco componentize dist/plugin.bundle.js \
  --wit wit --world-name hellohq-plugin-component \
  -o my_plugin.component.wasm

# 3. inspect imports/exports
wasm-tools component wit my_plugin.component.wasm   # or: npx jco wit ...
```

See the example's [`build.sh`](../../examples/component-quickstart-js/build.sh).

### ⚠️ JS plugins require host WASI

jco embeds the **StarlingMonkey JS engine** into every component. The built
component therefore imports a `wasi:*` surface
(`wasi:io/cli/clocks/filesystem/random/http`) **in addition** to the
`hellohq:plugin/*` capabilities — the engine needs it to run. **The host must
provide a WASI environment (e.g. `wasmtime-wasi`) to instantiate a JS plugin.**
This is the key difference from the no-std Rust guests, which import *only*
`hellohq:plugin/*` and need no WASI. JS components are also large (~12 MB, the
engine) vs a few KB for Rust.

The host still gates every capability: an import the host does not wire is
structurally unreachable. `wasi:sockets`/`wasi:filesystem` write access is never
granted app-side.

### ⚠️ Inference / streaming is not buildable yet

`hq.inference.complete` is declared against the canonical WIT
(`-> stream<string>`). **componentize-js 0.21.0 cannot componentize a world that
imports a WIT `stream` type** — it panics in the StarlingMonkey embedding
splicer. The default build world (`hellohq-plugin-component`) therefore omits
`inference`, and `hq.inference.*` is loaded lazily so plugins that don't use it
build cleanly. A plugin that *does* call `hq.inference.*` cannot be built into a
component on this toolchain until componentize-js gains `stream` support. (The
Rust SDK has the same sync/async split for the canonical `guest.run`.) See
[`wit/README.md`](./wit/README.md).

### Vendored WIT

[`wit/hellohq-plugin.wit`](./wit/) is vendored from the SSOT,
`HelloHQ/plugin-protocol`. Re-sync with `./scripts/sync-wit.sh` (or
`npm run sync:wit`). `wit/component.wit` is the SDK-local build world. See
[`wit/README.md`](./wit/README.md).

### Toolchain versions

Pinned / verified on this machine:

| Tool | Version |
|---|---|
| `@bytecodealliance/jco` | 1.24.1 |
| `@bytecodealliance/componentize-js` | 0.21.0 |
| `wasm-tools` | 1.252.0 |
| Node | 22.x |

---

## WebView UI (`@hellohq/plugin-sdk`)

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
