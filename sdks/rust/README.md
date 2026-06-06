# hellohq-plugin-sdk (Rust)

Build Tier 2 (Wasm) HelloHQ plugins in Rust. Compiles to `wasm32-wasip1`,
runs in-process in the host via Wasmtime, starts in <50ms, works on mobile.

```bash
rustup target add wasm32-wasip1
cargo build --target wasm32-wasip1 --release
```

```rust
use hellohq_plugin_sdk::{plugin, PluginError};

plugin! {
    fn run(input: &[u8]) -> Result<Vec<u8>, PluginError> {
        // `input` is JSON: { "function": "...", "args": {...} }
        // return JSON conforming to the declarative UI schema.
        Ok(br#"{"type":"text","content":"Hello from Rust"}"#.to_vec())
    }
}
```

## Linear-memory ABI

The `plugin!` macro exports `hq_plugin_run(ptr, len) -> i64` (packed
`ptr<<32 | len`) plus `hq_alloc`/`hq_free`. The host writes the input into a
buffer from `hq_alloc`, calls `hq_plugin_run`, reads the returned slice from
the module's `memory`, then calls `hq_free`. This matches the host
implementation in `PluginWasmService`.

Host imports (portfolio reads, file output) are generated from the WIT in
[`HelloHQ/plugin-protocol`](https://github.com/HelloHQ/plugin-protocol).
