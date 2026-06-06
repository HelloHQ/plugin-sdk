# ABI

The plugin ABI is defined canonically in
[`HelloHQ/plugin-protocol`](https://github.com/HelloHQ/plugin-protocol):

- `wit/hellohq-plugin.wit` — Tier 2 WIT contract
- `sidecar/*.schema.json` — Tier 1 NDJSON schemas

This directory holds **generated** bindings only; the protocol repo is the
source of truth. Regenerate after bumping the pinned protocol version:

```bash
# from repo root, with plugin-protocol checked out alongside
make gen-bindings   # wit-bindgen (rust/c) + jco (ts), output into each sdk
```

Pinned protocol version: see `PROTOCOL_VERSION` in each SDK package.
Do not hand-edit generated files.
