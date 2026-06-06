# Contributing to the HelloHQ Plugin SDK

This is a polyglot monorepo. Each SDK is independently versioned and published.

| Path | Toolchain | Publishes to |
|---|---|---|
| `sdks/python` | Python 3.11+, hatchling | PyPI: `hellohq-plugin-sdk` |
| `sdks/rust` | Rust, `wasm32-wasip1` | crates.io: `hellohq-plugin-sdk` |
| `sdks/js` | Node 20+, tsc | npm: `@hellohq/plugin-sdk` |
| `sdks/go` | Go 1.24+, `wasip1` | Go module |
| `cli` | Dart 3.4+ | pub.dev + Homebrew |
| `mock-host` | Dart 3.4+ | (internal, used by `cli`) |

## Ground rules

- **The protocol is upstream.** Types and the wire format are defined in
  [`HelloHQ/plugin-protocol`](https://github.com/HelloHQ/plugin-protocol). Do not
  fork types here — regenerate bindings into `abi/` and pin the protocol version.
- **Keep the linear-memory ABI identical across Tier 2 SDKs.** `hq_alloc` /
  `hq_plugin_run(ptr,len)->i64` / `hq_free` must match `PluginWasmService` in the
  host app. Changing it is a protocol change.
- **Each SDK ships its own tests.** Run them before opening a PR:
  - python: `cd sdks/python && python -m pytest` (or pipe fixtures, see README)
  - rust: `cd sdks/rust && cargo test`
  - js: `cd sdks/js && npm test`
  - dart: `cd cli && dart test`

## Commit / PR

Conventional commits (`feat(python): ...`, `fix(rust): ...`). One SDK per PR
where practical.
