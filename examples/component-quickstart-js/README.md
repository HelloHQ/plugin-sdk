# component-quickstart-js

The JS/TS twin of [`component-quickstart`](../component-quickstart) (Rust). A
minimal Tier-2 HelloHQ plugin built with `@hellohq/plugin-sdk/component` against
the canonical `hellohq:plugin@0.1.0` WIT, componentized with
[`jco`](https://github.com/bytecodealliance/jco) + `componentize-js`.

On `run` it logs a banner, reads workspace portfolio names, does a storage
set/get round-trip, emits an event, and returns a compact summary
`"<n-portfolios>|<roundtrip-ok>"` (e.g. `"3|1"`) — identical behavior to the Rust
quickstart.

## Build

```sh
npm install
./build.sh        # or: npm run build
```

`build.sh`:

1. **esbuild** bundles `src/plugin.ts` → `dist/plugin.bundle.js`, inlining
   `@hellohq/plugin-sdk` and keeping the host virtual imports (`hellohq:*` /
   `wasi:*`) **external**. jco's StarlingMonkey loader only resolves the single
   entry module plus those virtuals, so the SDK must be bundled into one file
   first.
2. **`jco componentize`** wraps the bundle into a component against the SDK's
   vendored WIT, building the **`hellohq-plugin-component`** world (the canonical
   interfaces **minus `inference`** — see the SDK's
   [`wit/README.md`](../../sdks/js/wit/README.md) for the componentize-js
   `stream` blocker).
3. **`wasm-tools component wit`** prints the component's imports/exports.

```sh
npx jco componentize dist/plugin.bundle.js \
  --wit ../../sdks/js/wit \
  --world-name hellohq-plugin-component \
  -o component_quickstart_js.component.wasm
```

## Verified component shape

The plugin touches only `workspace`/`storage`/`events`/`log`, so it imports just
that subset of `hellohq:plugin/*@0.1.0` (plus `types`). **Unlike the Rust
quickstart, the component ALSO imports a `wasi:*` surface** — jco embeds the
StarlingMonkey JS engine, which needs a WASI environment at runtime. The host
must therefore provide WASI (e.g. `wasmtime-wasi`) to run a JS plugin; a no-std
Rust guest needs none.

```
world root {
  // hellohq custom capabilities (host-implemented + permission-gated)
  import hellohq:plugin/types@0.1.0;
  import hellohq:plugin/workspace@0.1.0;
  import hellohq:plugin/storage@0.1.0;
  import hellohq:plugin/events@0.1.0;
  import hellohq:plugin/log@0.1.0;

  // pulled in by the embedded StarlingMonkey JS engine (NOT by the plugin)
  import wasi:io/error@0.2.10;
  import wasi:io/poll@0.2.10;
  import wasi:io/streams@0.2.10;
  import wasi:cli/stdin@0.2.10;
  import wasi:cli/stdout@0.2.10;
  import wasi:cli/stderr@0.2.10;
  import wasi:cli/terminal-input@0.2.10;
  import wasi:cli/terminal-output@0.2.10;
  import wasi:cli/terminal-stdin@0.2.10;
  import wasi:cli/terminal-stdout@0.2.10;
  import wasi:cli/terminal-stderr@0.2.10;
  import wasi:clocks/monotonic-clock@0.2.10;
  import wasi:clocks/wall-clock@0.2.10;
  import wasi:filesystem/types@0.2.10;
  import wasi:filesystem/preopens@0.2.10;
  import wasi:random/random@0.2.10;
  import wasi:http/types@0.2.10;
  import wasi:http/outgoing-handler@0.2.10;

  export hellohq:plugin/guest@0.1.0;
}
```

The built component is ~12 MB (it embeds the JS engine), versus a few KB for the
no-std Rust guest. That is the cost of a JS runtime; it is acceptable for Tier-2
authoring ergonomics.

> `wasi:filesystem` and `wasi:http` are imported by the engine's baseline WASI
> shim even though this plugin never uses them. They can be partially trimmed
> with `jco componentize --disable http` etc., but `wasi:filesystem/*` and
> `wasi:http/types` remain in StarlingMonkey 0.21.0. The host still gates every
> capability: an import the host does not wire is unreachable.
