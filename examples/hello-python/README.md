# hello-python

`com.hellohq.hello-python`: the minimal reference **Tier-1 (Python sidecar)**
plugin, published as an Official plugin in the public registry
([HelloHQ/plugin-registry](https://github.com/HelloHQ/plugin-registry)). It is
the Python counterpart of [`hello-world`](../hello-world) (Tier-2, Rust/Wasm)
and does the same thing.

## What it does

On `run` it lists the workspace's portfolio names as a declarative UI
document:

```json
{
  "type": "column",
  "children": [
    { "type": "heading", "level": 1, "text": "Hello, Python!" },
    { "type": "text", "color": "muted", "content": "You have 2 portfolios in this workspace." },
    {
      "type": "table",
      "columns": [{ "key": "name", "label": "Portfolio" }],
      "rows": [{ "name": "Personal" }, { "name": "Business" }]
    }
  ]
}
```

With no portfolios it shows an `empty-state` ("No portfolios yet"); when the
host does not hand over the names, an `empty-state` saying they are
unavailable. The table is capped at the app's 500-row limit, with a note.

Its whole surface:

- **One permission**, `read:portfolio_names`. No `network:fetch`, no
  `plugin:storage`, no `ai:inference`, no `propose:*`.
- **No host call.** A Tier-1 plugin has no portfolio host call: the app reads
  the names before the run and passes them in
  `args["context"]["read:portfolio_names"]` (a list of `{"id", "name"}`); a
  read that is not granted is simply absent, which is the "unavailable" state.
- **Standard library only**: the shipped `plugin.py` imports `json`, `sys`,
  `typing` and `__future__`, and opens no file, socket or process.

### How it reaches the screen (`ui_type: "declarative"`)

Matched against the HelloHQ app (HelloHQ/hellohq):

1. `plugin_runner_view.dart` runs a sidecar row through
   `plugin_tier1_executor.dart`, with the first input
   `{"function":"main","args":{}}`.
2. `plugin_sidecar_bridge.dart` calls the sidecar's RPC function `run` with
   `{"context": <pre-fetched reads>, "input": <that input>}`.
3. The RPC `result` (a JSON object, one component `{"type": ...}`) is handed to
   `plugin_declarative_renderer.dart`, which draws `column`, `heading`, `text`,
   `table` and `empty-state` with the fields used above.

## Layout

| File | Purpose |
|---|---|
| `hello_python.py` | The plugin: the pure UI builder `portfolio_names_ui`, the context reader and `dispatch` |
| `bundle.py` | Joins the SDK's sidecar loop and `hello_python.py` into one `plugin.py` |
| `build.sh` | Builds `plugin.py` (the `wasm_url` asset), byte-compiles it, prints its sha256 |
| `icon.svg` | The 24x24 single-path sidebar icon (the `sidebar_icon` asset) |
| `manifest.json` | Same manifest as the registry entry (placeholder hash) |
| `tests/` | UI, manifest, bundle and end-to-end sidecar tests |

`plugin.py` is build output (gitignored).

### Why a bundle

The host runs **one file** with nothing installed: it downloads only
`wasm_url`, runs it as `python -I` (no `PYTHONPATH`, no current directory on
`sys.path`), and the Python SDK is not on PyPI. So `bundle.py` copies the
SDK's `hellohq_plugin_sdk/protocol.py` and `hellohq_plugin_sdk/sidecar.py`
(the NDJSON `serve` loop) verbatim, minus their package imports, ahead of
`hello_python.py`. The SDK's host-call module is not included because the
plugin makes no host call. The output depends only on those three files: no
timestamps, no paths, `\n` line endings on every OS, so two builds produce
the same bytes.

## Build and test

```sh
./build.sh                       # -> plugin.py, prints its sha256 and size
uv sync --group dev && uv run pytest
uv run ruff check . && uv run ruff format --check .

# From the repo root, through the CLI's mock sidecar host (it sends an empty
# context, so this shows the "unavailable" state):
dart run cli/bin/hqplugin.dart test --sidecar examples/hello-python/plugin.py \
  --input '{"function":"main","args":{}}'
```

The tests:

- `test_ui.py`: the UI builder with N names, one, zero, the refusal, hostile
  strings and the row cap; the context reader; `dispatch`.
- `test_sidecar_e2e.py`: the bundled `plugin.py` as a real process, driven by
  `tests/host_double.py`, a double of the app's sidecar host: the same `-I`
  launch, network audit hook and context snapshot, the `ready` / `run` /
  `ping` / `shutdown` exchange with the app's timeouts, and every message
  validated against plugin-protocol's sidecar schemas. A recording audit hook
  checks that the plugin opens no file and touches no socket or process, and
  that it sends no host call; a negative control proves those checks fire.
- `test_bundle.py`: the bundle imports only `__future__`, `json`, `sys` and
  `typing` (parsed from its AST), calls no `open` / `exec` / `eval`, carries
  no host-call code, compiles, and two builds are byte-identical.
- `test_manifest.py`: the manifest against plugin-registry's schema and its
  CI rules for a sidecar (Official tier, `min_host_version` >= 2.0.0), the
  release URLs, and the icon against the registry's SVG rules.

The schema tests find plugin-protocol and plugin-registry through
`HELLOHQ_PLUGIN_PROTOCOL_DIR` / `HELLOHQ_PLUGIN_REGISTRY_DIR` or sibling
checkouts; CI sets `HELLOHQ_REQUIRE_PROTOCOL=1` / `HELLOHQ_REQUIRE_REGISTRY=1`
so a missing checkout fails instead of skipping. CI (`.github/workflows/ci.yml`)
runs them in the `examples` job on Python 3.11 and 3.12, and runs the built
`plugin.py` through `hqplugin test` on Linux, macOS and Windows.

## Release

Released by [`release-plugin.yml`](../../.github/workflows/release-plugin.yml)
when a `hello-python-v<version>` tag is pushed; see "Releasing an example
plugin" in [CONTRIBUTING.md](../../CONTRIBUTING.md). The workflow runs
`build.sh` on the tagged source and publishes `plugin.py`, `icon.svg` and
`SHA256SUMS`, each with a build provenance attestation. Before staging it
checks that the sidecar asset compiles and imports only the standard library.

`manifest.json` here keeps the all-zero placeholder `content_hash_sha256` and
no `sidebar_icon_hash_sha256`; `hqplugin publish --first-party` downloads the
released files and pins both hashes in the registry's copy. Keep `icon.svg` a
plain single-path SVG so it passes the registry's SVG rules.

The manifest has no `python_runtime`: there are no dependencies to install
(the app then uses its default `requires_python`, `>=3.11`), and the registry
schema has no such field. `min_host_version` is `2.0.0` because the registry's
CI requires it for a sidecar.
