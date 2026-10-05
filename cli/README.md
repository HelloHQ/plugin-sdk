# hqplugin CLI

Build, test, and publish HelloHQ plugins.

```bash
dart pub get
dart run bin/hqplugin.dart --help

# Build: compile a Rust crate to the Tier-2 Wasm the host loads.
dart run bin/hqplugin.dart build --lang rust --entry path/to/crate --out plugin.wasm

# Test: run it locally through a real Wasm runtime + the mock host.
dart run bin/hqplugin.dart test --wasm plugin.wasm --grant read:portfolio_names

# Publish: pin the released files in the plugin registry (plan only).
dart run bin/hqplugin.dart publish --dry-run
```

## `test`

`test --wasm <file> --grant <perm> [--grant <perm> ...]` runs the plugin through
a real, vendored Wasm runtime (Wasmtime, the same engine the app uses), wiring
the [`mock-host`](../mock-host) as the `env.hq_read` backend, and pretty-prints
the declarative tree the plugin returned (plus any emitted events).

- `--fixture <json>` seeds portfolios/currencies (defaults to a small demo set).
- `--input <json>` sets the run input (default `{"function":"main","args":{}}`).
- Execution is fuel-bounded, so a runaway plugin traps rather than hanging.

The runtime needs `libwasmtime`, provisioned by
`scripts/fetch-wasmtime-libs.sh` (or point `$HQPLUGIN_WASMTIME_LIB` at a copy).
Because the mock serves the exact `hq_read` protocol the real host does, a plugin
that renders correctly here renders correctly in the app.

### Tier 1 sidecars (`--sidecar`) and proposing

`test --sidecar <plugin.py|dir> --grant <perm> ...` spawns a Python sidecar and
answers its host calls with the mock host (`ai_complete`, `storage_*`,
`http_request`, `propose`). `--input <json>` is sent as the run's `input`.

A plugin that proposes holdings or values needs the propose permission **with
its asset kinds**, as the manifest's `scope.kinds` has them:

```bash
dart run bin/hqplugin.dart test --sidecar examples/wallet-tracker \
  --grant network:fetch --grant propose:holdings=crypto_ticker \
  --input '{"function":"scan","args":{"btc_addresses":["..."]}}'
```

The mock host validates the batch with the host's closed set of reason codes,
queues it in memory and prints what it queued; nothing is saved. A bare
`propose:holdings` (no kinds) is not a grant, as in the real host. See
[`mock-host`](../mock-host) for what the mock does and does not model.

## `build`

`build --lang rust --entry <crate-dir> --out <file>` runs
`cargo build --target wasm32-unknown-unknown --release`, copies the resulting
`.wasm` to `--out`, and componentizes it. It reports clear errors for a missing
`Cargo.toml`, a missing toolchain (`cargo`), or a missing target (`rustup target
add wasm32-unknown-unknown`). (Pass the **leaf** crate directory as `--entry`;
Cargo workspaces share a target dir.)

`--lang go` compiles the package with `GOOS=wasip1 GOARCH=wasm go build`.
`--lang typescript|python` are recognised but not yet wired.

### Streaming inference (`--inference`)

`--inference` builds the streaming-inference variant (async `run` draining
`inference.complete`'s `stream<string>`). For **Rust** the world is selected in
the crate's `wit_bindgen::generate!`, so the build command is unchanged. For
**Go** it uses the currently-unreleased toolchain (see
`examples/component-quickstart-go/inference`):

```bash
dart run bin/hqplugin.dart build --lang go --inference \
  --entry path/to/go-plugin \
  --wit ../sdks/go/wit \                     # or $HQ_PLUGIN_WIT — defines hellohq-plugin-inference
  --go /path/to/go-wasi-on-idle/bin/go \     # or $HQ_GO_WASI_ON_IDLE (dicej/go fork)
  --adapter /path/to/wasi_snapshot_preview1.reactor.wasm  # or $HQ_WASI_ADAPTER
```

It compiles the `wasip1` core with the fork (`-buildmode=c-shared`), embeds the
WIT against the `hellohq-plugin-inference` world, and adapts it into a Component.
Each required input has a clear error if missing. Plain `go` compiles but the
component traps at runtime in the stream wait, so the fork is required.

## `publish`

`publish` pins a released plugin in the
[plugin registry](https://github.com/HelloHQ/plugin-registry) and opens (or
updates) the registry pull request. Run it from the plugin directory (the one
with `manifest.json`).

The registry pins the SHA-256 of what `wasm_url`, `ui_bundle_url` and an https
`sidebar_icon` **serve**, so publish never hashes a local file. It downloads each URL (https only,
redirects followed, at most 64 MiB, the registry's limit), checks that a Wasm
plugin starts with the `\0asm` magic number (a sidecar ships its `.py`), and
pins the hash of those bytes. The all-zero placeholder hash is never pinned.

```bash
# The manifest's wasm_url (and ui_bundle_url) already point at released files:
hqplugin publish                        # plan: download, hash, diff, PR preview
hqplugin publish --submit               # open the registry PR

# Create the GitHub Release from the local files first:
hqplugin publish --release --bump patch --submit
```

### Where the URLs come from

- **Default**: the manifest's `wasm_url` / `ui_bundle_url` / `sidebar_icon`,
  as released.
- **`--release`**: creates a GitHub Release on the plugin repo with
  `gh release create <tag> <files> --target <HEAD>` and points the URLs at
  `https://github.com/<repo>/releases/download/<tag>/<file>`, then downloads
  them again and pins what GitHub serves.
  - `--repo owner/name` (default: from `git remote get-url origin`),
    `--tag` (default `v<version>`), `--wasm` (default `./plugin.wasm`, or
    `./plugin.py` for a sidecar), `--ui-bundle` (default `./ui.zip` when
    `ui_type` is `webview`), `--icon` (see the sidebar icon below).
  - Releases are immutable. If the tag already exists, nothing is uploaded:
    its assets are downloaded and reused when byte-identical to the local
    files, and publish fails otherwise (bump the version).
  - The plugin repo's git tree must be clean (`--allow-dirty` to override).

### Sidebar icon

The app draws a local copy of the icon that it downloaded once at install
and checked against `sidebar_icon_hash_sha256`; it never loads an unpinned
icon. So:

- An **https** `sidebar_icon` is downloaded (at most 64 KiB) and must be a
  plain SVG: no `<script>`, `<foreignObject>`, event-handler attribute,
  DOCTYPE/entity, `@import`, `javascript:` URL, external `href` / `url()`, or
  element that loads content (`image`, `use` of another file, `style`, ...).
  Publish pins `sidebar_icon_hash_sha256` from the served bytes. The rules live
  in `lib/src/publish_icon.dart`, mirroring the registry's
  `scripts/verify-artifacts.mjs` (the source of truth).
- A **relative** `sidebar_icon` is a path inside a WebView plugin's UI bundle:
  the bundle's hash covers it, so it gets no icon hash (a stale one is
  dropped).
- Any other scheme (`http:`, `data:`, `file:` ...) is refused.

With `--release`, an https `sidebar_icon` is re-released with the plugin: the
local icon (`--icon <path>`, default `./icon.svg`) is uploaded and
`sidebar_icon` is pointed at the new release, then round-trip hashed. If there
is no local icon, the existing URL is pinned as served, with a warning when it
points at another release. A manifest without `sidebar_icon` gets one only from
an explicit `--icon`; a stray `./icon.svg` is never added on its own.

### Version

`--version X.Y.Z` or `--bump patch|minor|major` (from the manifest's
`version`; not both). Without either, the manifest's `version` is used. The
release tag and the registry manifest use the same version. A version must be
higher than the registry's; re-running a publish whose result the registry
already has is a no-op.

### The registry copy of the manifest

- `signatures` are always removed; the registry's signing pipeline signs after
  merge.
- `trust_tier` and `publisher_signing_key_id` are set by the registry team: on
  an update they are copied from the registry's current manifest, for a new
  plugin they are removed.
- `provenance: enterprise` and commercial licensing are refused.
  `provenance: core` is refused unless `--first-party`.
- `--first-party` (HelloHQ maintainers; checked with
  `gh api orgs/HelloHQ/members/<login>`) allows `core` and keeps the
  manifest's `trust_tier`. Registry CI and review remain the real gate.

### `--submit`

Needs `gh` (logged in) and `node`. Publish forks the registry if needed,
clones the fork, and on branch `publish/<id>/<version>` (from upstream `main`)
writes `plugins/<id>/manifest.json` (2-space JSON). It then runs the registry's
own scripts from the clone:

- `node scripts/build-index.mjs` (the regenerated `index.json` is committed);
- `node scripts/check-pr-signatures.mjs`;
- `node scripts/verify-artifacts.mjs` (a missing `wasm-tools` is only a
  warning, since CI runs it; any other failure stops before the push).

The branch is force-pushed, so re-running the same version updates the open
PR instead of opening another. The PR is titled `Add plugin: <id> <version>`
or `Update plugin: <id> <old> → <new>`; its body lists the pinned hashes and
their URLs, the permission changes on an update, and a note when the plugin
declares Verified-only capabilities (the registry team assigns the tier).

### Plan only

Without `--submit`, or with `--dry-run`, nothing is changed: no release, fork,
push or PR. Publish still downloads and hashes the artifacts, diffs the
registry manifest against the registry's `main`, and prints the PR it would
open. For a release that does not exist yet, the plan shows the local file's
hash; the real pin comes from the download after the release is created.

Exit codes: 64 usage, 65 invalid data, 66 missing input, 69 tool or service
unavailable, 77 not permitted (`--first-party` for a non-member).

`test/publish_network_test.dart` dry-runs against the real released
hello-world; it runs only with `HQPLUGIN_NETWORK_TESTS=1`.

> Distributed via pub.dev and Homebrew when complete.
