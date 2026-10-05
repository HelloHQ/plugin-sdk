# hello-world

`com.hellohq.hello-world` — the minimal reference plugin listed as an
Official plugin in the public registry
([HelloHQ/plugin-registry](https://github.com/HelloHQ/plugin-registry),
`plugins/com.hellohq.hello-world/manifest.json`).

A Tier-2 `hellohq:plugin@0.1.0` component written in Rust with
[`hellohq-plugin-sdk`](../../sdks/rust) (`no_std`, `setup_guest!`,
`export_plugin!`, like [`component-quickstart`](../component-quickstart)).

## What it does

On `run` it reads the workspace's portfolio names and returns a declarative UI
document listing them:

```json
{
  "type": "column",
  "children": [
    { "type": "heading", "level": 1, "text": "Hello, World!" },
    { "type": "text", "color": "muted", "content": "You have 2 portfolios in this workspace." },
    {
      "type": "table",
      "columns": [{ "key": "name", "label": "Portfolio" }],
      "rows": [{ "name": "Personal" }, { "name": "Business" }]
    }
  ]
}
```

With no portfolios it shows an `empty-state` ("No portfolios yet"); if the host
refuses the read, an `empty-state` saying the names are unavailable. Names are
JSON-escaped, and the table is capped at the app's 500-row limit with a note.

It declares one permission, `read:portfolio_names`, and calls nothing else, so
the built component imports only:

```
world root {
  import hellohq:plugin/types@0.1.0;
  import hellohq:plugin/workspace@0.1.0;
  import hellohq:plugin/log@0.1.0;

  export hellohq:plugin/guest@0.1.0;
}
```

The node types and fields it emits are the ones the HelloHQ app's declarative
renderer handles (`column`, `heading`, `text`, `table`, `empty-state`); see the
comment at the top of [`src/ui.rs`](src/ui.rs) for the app files it was
matched against.

## Layout

| File | Purpose |
|---|---|
| `src/ui.rs` | Pure UI-document builder + its unit tests |
| `src/lib.rs` | The component: reads names, returns the document |
| `build.sh` | Builds `plugin.wasm` (the `wasm_url` asset) |
| `icon.svg` | The 24×24 single-fill sidebar icon (the `sidebar_icon` asset) |
| `manifest.json` | Same manifest as the registry entry |
| `Cargo.lock` | Committed: the released binary's dependencies are pinned |
| `tests/e2e_check.py` | Runs `plugin.wasm` through `hqplugin test` and checks the UI |

## Build and test

```sh
rustup target add wasm32-unknown-unknown   # once; also needs wasm-tools >= 1.252
./build.sh                                 # -> plugin.wasm, prints its WIT + sha256
cargo test --locked                        # unit tests (host)

# From the repo root, with the CLI's Wasmtime lib fetched
# (scripts/fetch-wasmtime-libs.sh):
python3 examples/hello-world/tests/e2e_check.py --wasm examples/hello-world/plugin.wasm --wit
```

CI (`.github/workflows/ci.yml`) runs the unit tests and `build.sh`, checks the
import set, and runs the end-to-end check on Linux, macOS and Windows.

`build.sh` uses `cargo build --locked`. If a change to `sdks/rust` changes its
dependencies, refresh the lock file here (`cargo update -p hellohq-plugin-sdk`)
in the same pull request.

## Release

Released by [`release-plugin.yml`](../../.github/workflows/release-plugin.yml)
when a `hello-world-v<version>` tag is pushed; see "Releasing an example
plugin" in [CONTRIBUTING.md](../../CONTRIBUTING.md). The workflow builds
`plugin.wasm` from the tagged source and publishes `plugin.wasm`, `icon.svg`
and `SHA256SUMS`, each with a build provenance attestation.

`manifest.json` here keeps the all-zero placeholder `content_hash_sha256`: the
hash of the released `plugin.wasm` only exists after the release build. The
registry's copy of the manifest carries the released hash (copy it from the
release notes or `SHA256SUMS`).
