# hqplugin CLI

Build, test, and publish HelloHQ plugins.

```bash
dart pub get
dart run bin/hqplugin.dart --help

# Build: compile a Rust crate to the Tier-2 Wasm the host loads.
dart run bin/hqplugin.dart build --lang rust --entry path/to/crate --out plugin.wasm

# Test: run it locally through a real Wasm runtime + the mock host.
dart run bin/hqplugin.dart test --wasm plugin.wasm --grant read:portfolio_names

# Coming next:
#   hqplugin publish --version 1.0.0        # open a registry PR
```

## `test`

`test --wasm <file> --grant <perm> [--grant <perm> ...]` runs the plugin through
a real, vendored Wasm runtime (Wasmtime, the same engine the app uses), wiring
the [`mock-host`](../mock-host) as the `env.hq_read` backend, and pretty-prints
the declarative tree the plugin returned (plus any emitted events).

- `--fixture <json>` seeds portfolios/currencies (defaults to a small demo set).
- `--input <json>` sets the run input (default `{"function":"main","args":{}}`).
- Execution is fuel-bounded, so a runaway plugin traps rather than hanging.

The runtime needs `libwasmtime`, provisioned by
`scripts/fetch-wasmtime-libs.sh` (or point `$HQPLUGIN_WASMTIME_LIB` at a copy).
Because the mock serves the exact `hq_read` protocol the real host does, a plugin
that renders correctly here renders correctly in the app.

## `build`

`build --lang rust --entry <crate-dir> --out <file>` runs
`cargo build --target wasm32-unknown-unknown --release`, copies the resulting
`.wasm` to `--out`, and componentizes it. It reports clear errors for a missing
`Cargo.toml`, a missing toolchain (`cargo`), or a missing target (`rustup target
add wasm32-unknown-unknown`). (Pass the **leaf** crate directory as `--entry`;
Cargo workspaces share a target dir.)

`--lang go` compiles the package with `GOOS=wasip1 GOARCH=wasm go build`.
`--lang typescript|python` are recognised but not yet wired.

### Streaming inference (`--inference`)

`--inference` builds the streaming-inference variant (async `run` draining
`inference.complete`'s `stream<string>`). For **Rust** the world is selected in
the crate's `wit_bindgen::generate!`, so the build command is unchanged. For
**Go** it uses the currently-unreleased toolchain (see
`examples/component-quickstart-go/inference`):

```bash
dart run bin/hqplugin.dart build --lang go --inference \
  --entry path/to/go-plugin \
  --wit ../sdks/go/wit \                     # or $HQ_PLUGIN_WIT — defines hellohq-plugin-inference
  --go /path/to/go-wasi-on-idle/bin/go \     # or $HQ_GO_WASI_ON_IDLE (dicej/go fork)
  --adapter /path/to/wasi_snapshot_preview1.reactor.wasm  # or $HQ_WASI_ADAPTER
```

It compiles the `wasip1` core with the fork (`-buildmode=c-shared`), embeds the
WIT against the `hellohq-plugin-inference` world, and adapts it into a Component.
Each required input has a clear error if missing. Plain `go` compiles but the
component traps at runtime in the stream wait, so the fork is required.

## `publish`

Not yet implemented — will open a PR against the
[plugin-registry](https://github.com/HelloHQ/plugin-registry) (manifest
validation + hash + git).

> Distributed via pub.dev and Homebrew when complete.
