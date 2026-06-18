# HelloHQ Plugin SDK (Go)

Build **Tier 2 — Component Model + WASI 0.3** HelloHQ plugins in Go, against the
canonical `hellohq:plugin@0.1.0` WIT, using **TinyGo + wit-bindgen-go**.

This SDK has two paths:

- **Component mode (recommended, Tier 2):** compile to a WebAssembly
  **component** with TinyGo. The ergonomic API lives in
  [`hq`](./hq) and the guest entry point in [`component`](./component). This
  mirrors the Rust SDK.
- **Legacy core-module mode:** the original `package hellohq` (`sdk.go`,
  `host_stub.go`, `host_wasm.go`, `ui.go`) with `Register` + a JSON dispatch
  protocol, built with `GOOS=wasip1 GOARCH=wasm go build`. Still works,
  untouched; component mode is additive.

`ProtocolVersion` is `"0.1.0"` in both `package hellohq` and `package component`.

## Component-mode quickstart

```go
package main

import (
	"fmt"

	"github.com/HelloHQ/plugin-sdk/sdks/go/component"
	"github.com/HelloHQ/plugin-sdk/sdks/go/hq"
)

type myPlugin struct{}

func (myPlugin) Init() { hq.Log.Info("my-plugin: init") }

func (myPlugin) Run(_ []byte) ([]byte, error) {
	names, err := hq.Workspace.ReadPortfolioNames()
	if err != nil {
		return nil, err
	}
	if err := hq.Storage.Set("last", []byte("ok")); err != nil {
		return nil, err
	}
	_ = hq.Events.Emit("ran", []byte("ok"))
	return []byte(fmt.Sprintf("%d portfolios", len(names))), nil
}

func (myPlugin) Metadata() component.Metadata {
	return component.Metadata{ID: "my-plugin", Version: "0.1.0"}
}

func init() { component.Export(myPlugin{}) }

func main() {} // required by TinyGo; never called for a reactor component
```

Build it (see `examples/component-quickstart-go/build.sh` for a turnkey script):

```bash
tinygo build \
  -target=wasip2 \
  -wit-package ../../sdks/go/wit \
  -wit-world hellohq-plugin-tinygo \
  -o my-plugin.component.wasm .

wasm-tools component wit my-plugin.component.wasm   # inspect imports/exports
```

## Author API (`hq`)

Every call maps 1:1 onto a `hellohq:plugin/*@0.1.0` import and returns
`(value, *hq.Error)`. The host permission-gates each call; a denial surfaces as
an `*hq.Error` with a stable `Code` (e.g. `"permission-denied"`).

| Group          | Methods |
| -------------- | ------- |
| `hq.Workspace` | `ReadPortfolioNames`, `ReadSheetStructure`, `ReadAssetCount`, `ReadCurrencyRates`, `ReadAggregatedValues` |
| `hq.Storage`   | `Get`, `Set`, `Delete`, `Clear`, `ListKeys` |
| `hq.Events`    | `Emit(kind, payload)`, `EmitEvent(PluginEvent)` |
| `hq.Log`       | `Trace`, `Debug`, `Info`, `Warn`, `Error`, `Write(level, msg)` |

The generated record types (`PortfolioName`, `CurrencyRate`, `SheetSummary`,
`AssetCount`, `AggregatedSummary`, `PluginEvent`, …) are re-exported from the
`hq` package under stable names. The raw wit-bindgen-go bindings live in
`internal/bindings/` (escape hatch; most plugins never touch them).

## Inference is omitted (for now)

The canonical world imports `inference`, whose
`complete: func(..) -> result<stream<string>, api-error>` uses WASI-0.3
`stream`. **wit-bindgen-go v0.7.0 + TinyGo 0.41 cannot bind that `stream`**, so
the Go SDK builds against a supplementary world that imports the same
`hellohq:plugin/*@0.1.0` interfaces **minus `inference`** — exactly like the JS
SDK's `hellohq-plugin-component` world and the Rust SDK's sync-`run` split.
Interface identities are unchanged. When the toolchain gains WASI-0.3 `stream`
support, the Go path can add `inference` back. See `wit/component.wit`.

## The host needs WASI 0.2 for Go plugins

Unlike the Rust `no_std` path (which imports **zero** `wasi:*`), TinyGo's runtime
(its asyncify scheduler, GC, allocator, `runtime` package) emits a handful of
WASI-0.2 imports. The built component therefore imports both the gated
`hellohq:plugin/*` interfaces **and**:

```
wasi:cli/environment, wasi:cli/stdin, wasi:cli/stdout, wasi:cli/stderr,
wasi:clocks/monotonic-clock, wasi:clocks/wall-clock,
wasi:filesystem/types, wasi:filesystem/preopens,
wasi:io/error, wasi:io/streams, wasi:random/random
```

The HelloHQ host must provide a **WASI-0.2 environment** for Go plugins (a
minimal/stubbed one is fine — the plugin's *capabilities* are still only the
gated `hellohq:plugin/*` imports). `wasi:sockets` is **not** in the final import
set (TinyGo tree-shakes it). This is the documented cost of the Go path versus
the leaner Rust `no_std` components.

## WIT vendoring & regeneration

- `wit/hellohq-plugin.wit` — the canonical world, vendored verbatim from the
  `plugin-protocol` SSOT. Re-sync with `./scripts/sync-wit.sh` (mirrors the
  Rust/JS SDKs; only this file is overwritten).
- `wit/component.wit` — SDK-local build worlds (no `package` line; inherits
  `hellohq:plugin@0.1.0`): `hellohq-plugin-component` (the inference-free
  identity world used for binding generation) and `hellohq-plugin-tinygo` (the
  same plus `include wasi:cli/imports@0.2.0` so the TinyGo component encode
  resolves its runtime imports).
- `wit/deps/` — WASI-0.2 WIT vendored from TinyGo's `lib/wasi-cli`, needed only
  so `wasm-tools` / the TinyGo encode can resolve the WASI imports.
- `internal/bindings/` — committed wit-bindgen-go output. Regenerate with
  `./scripts/gen-bindings.sh` after a WIT sync.

## Toolchain

| Tool            | Version used | Install |
| --------------- | ------------ | ------- |
| TinyGo          | 0.41.1       | `brew tap tinygo-org/tools && brew install tinygo` |
| wit-bindgen-go  | v0.7.0       | `go install go.bytecodealliance.org/cmd/wit-bindgen-go@latest` |
| wasm-tools      | 1.252.0      | `cargo install wasm-tools` |
| `go.bytecodealliance.org/cm` | v0.3.0 | (go.mod dependency) |

Protocol: https://github.com/HelloHQ/plugin-protocol
