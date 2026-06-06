# hellohq-plugin-sdk (Rust)

Build Tier 2 (Wasm) HelloHQ plugins in Rust. Compiles to
`wasm32-unknown-unknown`, runs in-process in the host via Wasmtime, starts in
<50 ms, works on mobile.

```bash
rustup target add wasm32-unknown-unknown
cargo build --target wasm32-unknown-unknown --release
```

```rust
use hellohq_plugin_sdk::{plugin, host, ui, PluginError};

plugin! {
    fn run(_input: &[u8]) -> Result<Vec<u8>, PluginError> {
        // Permission-gated read (needs `read:portfolio_names` in the manifest).
        let portfolios = host::read_portfolio_names().unwrap_or_default();
        let items = portfolios.iter().enumerate()
            .map(|(i, p)| (format!("Portfolio {}", i + 1), p.name.clone()))
            .collect();
        Ok(ui::column(vec![
            ui::heading(&format!("Portfolios ({})", portfolios.len())),
            ui::key_value_list(items),
        ]).to_bytes())
    }
}
```

A complete, host-validated example lives in
[`examples/portfolio_overview`](../../examples/portfolio_overview).

## What the SDK gives you

| Module | Purpose |
|---|---|
| [`plugin!`] macro | Wires the `run` export — no pointers, no manual framing |
| `host::*` | Typed, permission-gated reads (`read_portfolio_names`, `read_sheet_structure`, `read_asset_count`, `read_currency_rates`, `read_aggregated_values`) + `host::emit` for WebView push events |
| `ui::*` | Builders for all 15 declarative components (column, heading, key-value-list, table, metric, button, select, badge-row, empty-state, …) |

## Linear-memory ABI

The `plugin!` macro exports `run(ptr, len) -> i64` (packed `(ptr << 32) | len`),
and the crate exports `alloc(len) -> ptr` plus the module `memory`. The host
(`PluginWasmService`) writes the input into an `alloc` buffer, calls `run`, and
reads the returned slice from `memory`. Data reads go through the
`env.hq_read(ptr, len) -> i64` import; push events through
`env.emit_event(i32,i32,i32,i32)`. The `host::*` functions wrap these — you
never touch them directly.

This is the ABI the host actually implements. (The richer per-function surface
in the [protocol WIT](https://github.com/HelloHQ/plugin-protocol) is the
canonical *design*; the SDK maps it onto the host's single JSON `hq_read`
dispatch.)

## Testing

The SDK's own unit tests (UI builders + ABI marshalling) run on the host arch:

```bash
cargo test
```

End-to-end validation — running an SDK-built plugin through the real host — is
in the host repo: `test/unit/service/plugin_sdk_rust_test.dart`.
