# HelloHQ Plugin SDK

Everything a plugin author needs to build, test, and publish a HelloHQ plugin —
across all supported languages — plus the `hqplugin` CLI and a mock host for
local testing.

```
sdks/
  python/     pip:    hellohq-plugin-sdk     (Tier 1 — Pyodide + Deno sidecar)
  rust/       crates: hellohq-plugin-sdk     (Tier 2 — Component Model + WASI 0.3, hellohq:plugin@0.1.0)
  js/         npm:    @hellohq/plugin-sdk    (Tier 2 — Wasm / WebView)
  go/         module: github.com/HelloHQ/plugin-sdk/go   (Tier 2 — Wasm)
cli/          hqplugin — build / test / publish (Dart)
mock-host/    in-process mock of the host ABI for local tests (Dart)
abi/          pointer to the protocol SSOT (HelloHQ/plugin-protocol)
examples/     end-to-end worked examples
```

## Which SDK?

| You need… | Tier | SDK |
|---|---|---|
| NumPy / pandas / scipy | 1 | `sdks/python` |
| Fast startup, mobile, Rust | 2 — **Component Model + WASI 0.3** (`hellohq:plugin@0.1.0`) | `sdks/rust` |
| Fast startup, mobile, Go | 2 — Wasm | `sdks/go` |
| JS/TS or a WebView UI | 2 — Wasm / WebView | `sdks/js` |

## Status

This is the initial scaffold (Phase 4 — *begin*).

| Component | Status |
|---|---|
| `sdks/python` | working sidecar runtime (ready/RPC/ping/shutdown) |
| `sdks/rust` | Component Model SDK against `hellohq:plugin@0.1.0` (`Plugin` trait + `export_plugin!`, `hq::*` capabilities, streaming inference); builds to a verified component |
| `sdks/js` | typed `HQHost` surface (skeleton) |
| `sdks/go` | module + dispatch skeleton |
| `cli` | `hqplugin` command surface (skeleton) |
| `mock-host` | host ABI mock (skeleton) |

## Protocol

The contract these SDKs implement lives in
[`HelloHQ/plugin-protocol`](https://github.com/HelloHQ/plugin-protocol).
See [`abi/README.md`](abi/README.md) for how bindings are generated.

## License

Apache 2.0 — see [LICENSE](LICENSE).
