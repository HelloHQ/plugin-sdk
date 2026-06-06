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
`cargo build --target wasm32-unknown-unknown --release` and copies the resulting
`.wasm` to `--out`. It reports clear errors for a missing `Cargo.toml`, a missing
toolchain (`cargo`), or a missing target (`rustup target add
wasm32-unknown-unknown`).

`--lang go|typescript|python` are recognised but not yet wired — only Rust is
implemented today. (Pass the **leaf** crate directory as `--entry`; Cargo
workspaces share a target dir.)

## `publish`

Not yet implemented — will open a PR against the
[plugin-registry](https://github.com/HelloHQ/plugin-registry) (manifest
validation + hash + git).

> Distributed via pub.dev and Homebrew when complete.
