# Streaming inference (the async path)

`inference.complete` **streams** token deltas (`stream<string>`). Draining a
stream *yields*, so it must run inside an `async` context. The canonical
`hellohq:plugin/guest` export — `run: func(...)` — is **sync**, so a plugin
built against the full `hellohq-plugin` world can *start* a completion (the
[`hq::inference::complete`] call) but cannot drain it from `run`.

To consume the stream end to end, build against the SDK's
**`inference-quickstart`** world (`sdks/rust/wit/quickstart.wit`), whose `run`
is an `async func`:

```wit
world inference-quickstart {
  import types;
  import log;
  import inference;
  export run: async func(input: list<u8>) -> result<list<u8>, string>;
}
```

A worked, building example lives in [`inference/`](./inference/). It:

- generates bindings with `wit_bindgen::generate!({ world: "inference-quickstart" })`,
- sends one user message,
- drains the `stream<string>` with `stream.collect().await`, concatenating the
  deltas,
- returns the completion text.

Build it:

```sh
cd inference
./build.sh
```

Verified component shape (no `wasi:*`):

```
world root {
  import hellohq:plugin/types@0.1.0;
  import hellohq:plugin/log@0.1.0;
  import hellohq:plugin/inference@0.1.0;
  export run: async func(input: list<u8>) -> result<list<u8>, string>;
}
```

## Why a separate world / crate

The async `run` export and the canonical sync `guest` export are mutually
exclusive shapes for a given component. The sync quickstart (the parent crate)
exports `guest`; the inference quickstart exports the async `run`. When the
host's runtime grows an inference-capable harness that also wants the
`guest` lifecycle (init/metadata) alongside an async run, the canonical WIT can
add an `async run` variant to `guest` — at which point the SDK's `Plugin` trait
can expose an `async fn run` directly and this narrow world goes away.

## Helper

For SDK-based async crates, [`hq::inference::collect`] drains a stream to a
`String`:

```rust
let stream = hq::inference::complete(&messages, opts)?;
let text: String = hq::inference::collect(stream).await;
```
