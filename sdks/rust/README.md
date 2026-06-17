# hellohq-plugin-sdk (Rust)

Build **Tier-2 HelloHQ plugins** as WebAssembly **components** against the
canonical [`hellohq:plugin@0.1.0`](./wit/hellohq-plugin.wit) WIT. Compiles to
`wasm32-unknown-unknown`, then `wasm-tools component new` wraps it into a
Component Model component the host loads via Wasmtime — no JIT on iOS (Pulley),
fast startup, mobile-friendly.

The host **implements and permission-gates every import**; an interface the
plugin never calls is tree-shaken out of the built component and is therefore
structurally unreachable.

> This crate (`0.2.x`) targets the Component Model. It supersedes the legacy
> core-module ABI (raw `wasm32-unknown-unknown` + a `{"method":…}` JSON
> `hq_read` protocol) shipped in `0.1.x` — there are no consumers of the legacy
> ABI.

## Quickstart

```rust
#![no_std]
extern crate alloc;
use alloc::{vec::Vec, string::String, format};
use hellohq_plugin_sdk::{hq, export_plugin, Plugin, PluginMetadata};

// dlmalloc global allocator + trapping panic handler (keeps the component
// free of any wasi import).
hellohq_plugin_sdk::setup_guest!();

struct MyPlugin;

impl Plugin for MyPlugin {
    fn init() {
        hq::log::info("my-plugin starting");
    }

    fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
        let names = hq::workspace::read_portfolio_names().map_err(|e| e.message)?;
        hq::storage::set("count", &[names.len() as u8]).map_err(|e| e.message)?;
        hq::events::emit("scanned", b"ok").ok();
        Ok(format!("{} portfolios", names.len()).into_bytes())
    }

    fn metadata() -> PluginMetadata {
        PluginMetadata { id: "my-plugin".into(), version: "0.1.0".into() }
    }
}

export_plugin!(MyPlugin);
```

A complete, component-verified example is in
[`examples/component-quickstart`](../../examples/component-quickstart).

## Build workflow

```bash
# one-time
rustup target add wasm32-unknown-unknown
cargo install wasm-tools          # or: brew install wasm-tools  (>= 1.252)

# build the core module, then wrap it into a component
cargo build --release --target wasm32-unknown-unknown
wasm-tools component new \
  target/wasm32-unknown-unknown/release/my_plugin.wasm \
  -o my_plugin.component.wasm

# inspect the component's imports/exports
wasm-tools component wit my_plugin.component.wasm
```

The crate must be a `cdylib` (`crate-type = ["cdylib"]`) so it produces a core
module for `wasm-tools component new`.

## API surface

All under the `hq` module — each function maps 1:1 onto a `hellohq:plugin/*`
import and returns `Result<…, ApiError>` (reads) where the host can deny.

| Module | Functions |
|---|---|
| `hq::workspace` | `read_portfolio_names`, `read_sheet_structure`, `read_asset_count`, `read_currency_rates`, `read_aggregated_values` |
| `hq::storage` | `get`, `set`, `delete`, `clear`, `list_keys` |
| `hq::events` | `emit(kind, payload)`, `emit_event(&PluginEvent)` |
| `hq::log` | `trace`, `debug`, `info`, `warn`, `error`, `write(level, msg)` |
| `hq::inference` | `complete(messages, opts) -> StreamReader<String>` (streaming), `collect(stream).await`, message builders `user`/`system`/`assistant` |

Plus:

- **`Plugin`** trait (`init` / `run` / `metadata`) + the **`export_plugin!`**
  macro that wires the canonical `hellohq:plugin/guest` exports.
- **`setup_guest!`** — installs the `dlmalloc` global allocator + a trapping
  panic handler (the `#![no_std]` recipe that keeps the component wasi-free).
- Clean re-exports of the generated records/errors: `ApiError`, `PortfolioName`,
  `CurrencyRate`, `SheetSummary`, `AssetCount`, `AggregatedSummary`,
  `PluginEvent`, `ChatMessage`, `InferenceOpts`, `PluginMetadata`, `LogLevel`.
- **`bindings`** — the raw `wit_bindgen`-generated module, an escape hatch if you
  outgrow `hq::*`.

## `#![no_std]`

The SDK is `#![no_std]` (it re-exports `alloc`). Plugins are `#![no_std]` too;
`setup_guest!` supplies the allocator + panic handler. This is why the built
component imports only `hellohq:plugin/*` and never pulls in `wasi:*`.

## Streaming inference

`hq::inference::complete` returns a `stream<string>` of token deltas. Draining
the stream **yields**, so it must run in an `async` context — and the canonical
`guest.run` export is **sync**. To stream end to end, build against the
`inference-quickstart` world (`wit/quickstart.wit`), whose `run` is an
`async func`. See
[`examples/component-quickstart/INFERENCE.md`](../../examples/component-quickstart/INFERENCE.md)
for a working example.

## Vendored WIT

[`wit/hellohq-plugin.wit`](./wit/) is vendored from the SSOT,
`HelloHQ/plugin-protocol`. Re-sync with `./scripts/sync-wit.sh`. See
[`wit/README.md`](./wit/README.md). (A submodule would be cleaner long-term —
tracked as a follow-up.)

## Testing

The SDK's own checks build on the host arch:

```bash
cargo build --release   # builds the lib + bindings on host arch
cargo test
```

Running a built component on the real host (`hellohq-wasm-runtime`) is a
separate integration; the runtime's harnesses use narrow test worlds today.
Building a valid, correctly-shaped component is the bar this SDK meets — see the
example's `build.sh` output.
