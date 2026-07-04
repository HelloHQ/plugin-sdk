# component-quickstart-go / inference (streaming)

The Go **streaming-inference** quickstart. Mirrors the Rust
[`../../component-quickstart/inference`](../../component-quickstart/inference):
on `run` it sends one user message, **drains the `inference.complete`
`stream<string>`** concatenating each token delta, and returns the completion as
bytes.

It builds against the SDK's `hellohq-plugin-inference` world
([`../../../sdks/go/wit/component.wit`](../../../sdks/go/wit/component.wit)),
whose `run` is an **`async func`** — required because draining the token stream
yields. A plain (non-inference) Go plugin keeps the sync `guest` export via
[`../`](..); this is the second, async mode.

## Why this build differs from the sync Go quickstart

The sync quickstart uses TinyGo + `wit-bindgen-go`. That path **cannot** consume
a streaming inference result: `go.bytecodealliance.org/cm`'s `cm.Stream[T]` has
**no read API**, and TinyGo's runtime can't yield a blocked goroutine to the
component-model async executor. Draining `stream<string>` from Go needs three
pieces — all currently **unreleased** (pinned git revs + a Go fork), fetched on
demand into `./.toolchain` (gitignored) by `build.sh`:

| Piece | What it provides |
|---|---|
| `bytecodealliance/wit-bindgen` Go backend (rev `e14b18ca`, `--features go`) | A readable `StreamReader[string]` (`Read` / `WriterDropped` / `Drop`) + goroutine-based concurrency on the Component Model concurrency ABI |
| `dicej/go` `go1.25.5-wasi-on-idle` fork | A blocking goroutine yields to the async executor (via a `runtime.wasiOnIdle` patch) instead of dead-locking |
| `wasi_snapshot_preview1.reactor` adapter (wasmtime `v39.0.1`) | Wraps the `GOOS=wasip1` core module into a Component Model component |

When these land in releases, this build collapses to roughly the size of the
sync one. Until then, the generated bindings (`wit_bindings.go`, `wit_types/`,
`wit_async/`, `hellohq_plugin_*/`) are **committed** so the example is
inspectable and buildable without re-running the generator — `build.sh` only
needs the Go fork + the adapter for a plain build.

The module is named `wit_component` (the wit-bindgen Go backend hardcodes it);
the hand-written guest lives in [`export_wit_world/run.go`](export_wit_world/run.go)
and is preserved across `--gen` regeneration.

## Build

```bash
./build.sh          # plain build (fetches Go fork + adapter into .toolchain/)
./build.sh --gen    # also regenerate the committed bindings (needs cargo)
```

Requires: `curl`, `tar`, `wasm-tools ≥ 1.252` (and `cargo` for `--gen`).

## Verified output

The component **exports** `run: async func(input: list<u8>) -> result<list<u8>, string>`
and **imports** only:

```
hellohq:plugin/types@0.1.0
hellohq:plugin/log@0.1.0
hellohq:plugin/inference@0.1.0
wasi:cli/… wasi:io/… wasi:clocks/… wasi:filesystem/… wasi:random/…   (from the preview1 adapter)
```

Component size: **~2.5 MB**. The `wasi:*` imports come from the preview1 adapter
(the Go runtime), not the plugin's capabilities — the HelloHQ host provides a
minimal WASI-0.2 environment, exactly as for the sync Go path. This component has
been run end-to-end against a wasmtime host (component-model-async) that produces
a host `stream<string>`; the guest drains it and returns the concatenation.
