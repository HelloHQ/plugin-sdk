# Vendored WIT — `hellohq:plugin@0.1.0`

`hellohq-plugin.wit` is **vendored** from the canonical source of truth:

> `HelloHQ/plugin-protocol` → `wit/hellohq-plugin.wit`

It is copied here because this SDK is a separate repository from
`plugin-protocol`. Do not hand-edit it — edit the SSOT and re-sync.

## Re-sync

```sh
./scripts/sync-wit.sh             # copies the SSOT over the vendored copy
```

(Assumes `plugin-protocol` is checked out next to `plugin-sdk`; override with
`PLUGIN_PROTOCOL=/path/to/plugin-protocol ./scripts/sync-wit.sh`.)

After syncing, validate:

```sh
wasm-tools component wit wit/
```

`sync-wit.sh` only overwrites `hellohq-plugin.wit`; `component.wit` is
SDK-local and is left untouched.

## Files

- **`hellohq-plugin.wit`** — the canonical package + the full `hellohq-plugin`
  world (imports `types`/`workspace`/`storage`/`events`/`log`/`inference`,
  exports `guest`). Pristine SSOT copy.
- **`component.wit`** — SDK-local supplementary world `hellohq-plugin-component`
  in the **same** `hellohq:plugin@0.1.0` package. It imports the canonical
  interfaces **minus `inference`** and is the world `jco componentize` builds
  against.

## Why a second world (the `inference` / `stream` blocker)

`jco componentize` builds against one world and eagerly processes every
interface that world imports. The canonical `hellohq-plugin` world imports
`inference`, whose streaming signature

```wit
complete: func(messages: list<chat-message>, opts: inference-opts)
  -> result<stream<string>, api-error>;
```

crashes the JS engine's binding generator on the toolchain pinned here:

```
thread '<unnamed>' panicked at crates/spidermonkey-embedding-splicer/src/bindgen.rs:857:18:
internal error: entered unreachable code
(jco componentize) RuntimeError: unreachable
```

componentize-js 0.21.0 / StarlingMonkey does not yet support WASI-0.3 `stream`
types in its binding generator. `component.wit` omits the `inference` import so
the stream type never reaches the splicer, while keeping every other interface's
canonical identity. This is the JS analogue of the Rust SDK's sync/async split
(Rust's canonical `guest.run` is also sync and cannot drain the stream; it ships
a separate `inference-quickstart` world). The SDK also defines a parallel
`hellohq-plugin-inference` world (async `run`) for the day this unblocks.

**What's blocked vs. what already exists.** The JS `stream<T>` *runtime* is done:
[`@bytecodealliance/preview3-shim`](https://github.com/bytecodealliance/jco/tree/main/packages/preview3-shim)
implements WASI-0.3 streams/futures (`StreamReader`/`StreamWriter`/`stream()`,
bridged to WHATWG `ReadableStream`). The remaining gap is the **guest engine** —
componentize-js must (a) generate bindings for the `stream` type (the splicer
crash above) and (b) drive a native JS `Promise` through the component-model-async
executor (otherwise the stream isn't pumped and the call hangs). That work is in
flight upstream (jco's p3 bindgen — "lift numeric p3 lists and streams as typed
arrays" — and [`dicej/componentize-js`](https://github.com/dicej/componentize-js),
same author as the Go wasi-on-idle fork) but **unreleased**.

When a componentize-js release ships `stream` support, JS plugins can target the
`hellohq-plugin-inference` world (or the canonical `hellohq-plugin`) directly and
`component.wit` can be removed.
