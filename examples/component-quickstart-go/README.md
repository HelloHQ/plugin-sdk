# component-quickstart-go

The HelloHQ Tier-2 **Component Model** quickstart, in Go (TinyGo). Mirrors
[`../component-quickstart`](../component-quickstart) (Rust) and
[`../component-quickstart-js`](../component-quickstart-js) (JS).

On `run` the plugin:

1. logs a banner (`hq.Log`),
2. reads the workspace portfolio names (`hq.Workspace`),
3. stores + reads back a value (`hq.Storage`),
4. emits an event (`hq.Events`),
5. returns a compact ASCII summary `"<n-portfolios>|<roundtrip-ok>"`.

It touches only `workspace` / `storage` / `events` / `log`, so the built
component imports only those `hellohq:plugin/*@0.1.0` interfaces (+ `types`),
plus the WASI-0.2 imports TinyGo's runtime needs.

This is the **sync** Go plugin. For the **streaming-inference** variant (async
`run` draining `inference.complete`'s `stream<string>`), see
[`inference/`](inference) — it uses a separate build world + toolchain because
the sync TinyGo/`wit-bindgen-go` path can't drain a stream.

## Build

```bash
./build.sh
```

Requires:

- **TinyGo ≥ 0.41** — `brew tap tinygo-org/tools && brew install tinygo`
- **wasm-tools ≥ 1.252** — `cargo install wasm-tools` (for the final inspection)
- wit-bindgen-go is **not** needed to build (the SDK bindings are committed).

The build uses TinyGo's native `wasip2` target against the SDK's vendored WIT
and the `hellohq-plugin-tinygo` build world (canonical hellohq interfaces minus
`inference`, plus `wasi:cli/imports@0.2.0` for the TinyGo runtime).

## Verified output

`build.sh` runs `wasm-tools component wit` on the result. The component
**exports** `hellohq:plugin/guest@0.1.0` and **imports**:

```
hellohq:plugin/types@0.1.0
hellohq:plugin/workspace@0.1.0
hellohq:plugin/storage@0.1.0
hellohq:plugin/events@0.1.0
hellohq:plugin/log@0.1.0
wasi:cli/environment@0.2.0
wasi:cli/stdin@0.2.0
wasi:cli/stdout@0.2.0
wasi:cli/stderr@0.2.0
wasi:clocks/monotonic-clock@0.2.0
wasi:clocks/wall-clock@0.2.0
wasi:filesystem/types@0.2.0
wasi:filesystem/preopens@0.2.0
wasi:io/error@0.2.0
wasi:io/streams@0.2.0
wasi:random/random@0.2.0
```

Component size: **~813 KB**. The `wasi:*` imports come from the TinyGo runtime
(not from the plugin's capabilities) — the HelloHQ host provides a minimal
WASI-0.2 environment for Go plugins. See the [SDK README](../../sdks/go/README.md)
for the full host-WASI discussion and why `inference` is omitted.
