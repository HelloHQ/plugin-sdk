# component-quickstart

A minimal Tier-2 HelloHQ plugin built with `hellohq-plugin-sdk` against the
canonical `hellohq:plugin@0.1.0` WIT, demonstrating the Component Model
workflow.

On `run` it logs a banner, reads workspace portfolio names, does a storage
set/get round-trip, emits an event, and returns a compact summary
`"<n-portfolios>|<roundtrip-ok>"` (e.g. `"3|1"`).

## Build

```sh
./build.sh
```

That runs:

```sh
cargo build --release --target wasm32-unknown-unknown
wasm-tools component new \
  target/wasm32-unknown-unknown/release/component_quickstart.wasm \
  -o component_quickstart.component.wasm
wasm-tools component wit component_quickstart.component.wasm
```

## Verified component shape

The plugin only touches `workspace`/`storage`/`events`/`log`, so
`wasm-tools component new` tree-shakes everything else (including `inference`)
out. No `wasi:*` imports:

```
world root {
  import hellohq:plugin/types@0.1.0;
  import hellohq:plugin/workspace@0.1.0;
  import hellohq:plugin/storage@0.1.0;
  import hellohq:plugin/events@0.1.0;
  import hellohq:plugin/log@0.1.0;

  export hellohq:plugin/guest@0.1.0;
}
```

## Streaming inference

See [`INFERENCE.md`](./INFERENCE.md) and the [`inference/`](./inference/)
sub-crate for the async streaming-completion path.
