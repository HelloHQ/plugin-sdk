# Streaming-inference build toolchain

The pinned, cross-OS toolchain for building **streaming-inference** plugins
(`inference.complete -> stream<string>`) while the upstream
component-model-async toolchains are unreleased. Strategy and rationale:
[`../../docs/adr/0001-streaming-build-toolchain.md`](../../docs/adr/0001-streaming-build-toolchain.md).

TL;DR — we **mirror + patch**, we do **not** fork-to-diverge. Sync plugins don't
need any of this; they build with the released native toolchains on every OS.

## Pieces

| File | Purpose |
|---|---|
| `mirror-upstream.sh` | Create/sync HelloHQ GitHub mirrors of the exact pinned revs (run with org creds). |
| `Dockerfile` | One image that builds **both** Go and JS streaming plugins identically on macOS/Windows/Linux; bakes the `componentize-js` build (incl. the patch) once. |
| `patches/0001-pop-record-reverse-field-order.patch` | The one fix `dicej/componentize-js` needs (record field-order). |
| `upstream-pr.md` | Draft PR/issue to send that fix upstream. |

## One-time setup (maintainers)

```bash
# 1. Populate the HelloHQ mirror (needs gh + org repo-create rights).
MIRROR_ORG=HelloHQ ./mirror-upstream.sh

# 2. Build + publish the builder image (set WASI_SDK/GO_BOOT for your arch).
docker build -t hellohq/plugin-builder:0.1 .
docker push  hellohq/plugin-builder:0.1
```

## Author usage (any OS, just needs Docker)

The SDK wires this into the CLI:

```bash
hqplugin build --lang js --inference --entry ./my-plugin --out plugin.wasm   # uses the image
hqplugin build --lang go --inference --entry ./my-plugin --out plugin.wasm
```

Under the hood the image provides, on PATH / via env:
`componentize-js`, `wit-bindgen` (Go backend), `wasm-tools`,
`$HQ_GO_WASI_ON_IDLE` (the Go fork), `$HQ_WASI_ADAPTER` (preview1 reactor).
See [`../../examples/component-quickstart-go/inference`](../../examples/component-quickstart-go/inference)
for the equivalent manual pipeline.

## Trust boundary

Build-time only. The runtime is **official wasmtime**; the build output is a
`.wasm` the registry re-hashes/signs and the host integrity-checks before
execution. The pinned mirror + checked-in patch make the build fully auditable.

## Retire when upstream releases

Delete this directory and switch to released `componentize-js` /
`wit-bindgen-go` when stream support ships — see the ADR's cut-over triggers.
