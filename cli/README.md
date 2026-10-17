# hqplugin CLI

Build, test, and publish HelloHQ plugins.

A `cli/vX.Y.Z` tag builds Linux, macOS, and Windows binaries and attaches them
to a GitHub release. Until that tag exists, run it from this repo. It stays off
pub.dev: `cli/pubspec.yaml` depends on the in-repo mock host (`publish_to: none`).

```bash
dart pub get
dart run bin/hqplugin.dart --help

# Build: compile a Rust crate to the Tier-2 Wasm the host loads.
dart run bin/hqplugin.dart build --lang rust --entry path/to/crate --out plugin.wasm

# Test: run it locally through a real Wasm runtime + the mock host.
dart run bin/hqplugin.dart test --wasm plugin.wasm --grant read:portfolio_names

# Publish: pin the released files in the plugin registry (plan only).
dart run bin/hqplugin.dart publish --dry-run

# Author keys: create the hybrid key pair, then sign what you publish.
dart run bin/hqplugin.dart keys generate --out ~/.hqplugin/keys
dart run bin/hqplugin.dart publish --sign-key ~/.hqplugin/keys --submit
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
  merge. (An author who holds a key adds `author-signatures.json` beside the
  manifest instead: see [Author signatures](#author-signatures).)
- `trust_tier` and `publisher_signing_key_id` are set by the registry team: on
  an update they are copied from the registry's current manifest, for a new
  plugin they are removed.
- `provenance: enterprise` and commercial licensing are refused.
  `provenance: core` is refused unless `--first-party`.
- `--first-party` (HelloHQ maintainers; checked with
  `gh api orgs/HelloHQ/members/<login>`) allows `core` and keeps the
  manifest's `trust_tier`. Registry CI and review remain the real gate.
  First-party plugins are signed by the HelloHQ signer, so `--first-party`
  cannot be combined with `--sign-key` (exit 64).

### `--submit`

Needs `gh` (logged in) and `node`. Publish first asks GitHub whether you can
push to `HelloHQ/plugin-registry` (`gh api repos/HelloHQ/plugin-registry --jq
.permissions.push`) and says which path it takes:

- **Push access** (registry maintainers): it clones the registry itself and
  pushes the branch there; the PR's head is `publish/<id>/<version>` in the
  registry. No personal fork is created.
- **No push access**, or the check fails: it uses your fork
  `<login>/plugin-registry` (created on first use); the PR's head is
  `<login>:publish/<id>/<version>`.

In the clone, on branch `publish/<id>/<version>` (from upstream `main`), it
writes `plugins/<id>/manifest.json` (2-space JSON). It then runs the registry's
own scripts from the clone:

- `node scripts/build-index.mjs` (the regenerated `index.json` is committed);
- `node scripts/check-pr-signatures.mjs`;
- `node scripts/verify-artifacts.mjs` (a missing `wasm-tools` is only a
  warning, since CI runs it; any other failure stops before the push).

The branch is force-pushed, so re-running the same version updates the open
PR (found by its head, in either path) instead of opening another. Publish only
ever pushes a branch named exactly `publish/<id>/<version>`, with an explicit
`refs/heads/` refspec; anything else (such as `main`) is refused with exit 70. The PR is titled `Add plugin: <id> <version>`
or `Update plugin: <id> <old> → <new>`; its body lists the pinned hashes and
their URLs, the permission changes on an update, and a note when the plugin
declares Verified-only capabilities (the registry team assigns the tier).

### Plan only

Without `--submit`, or with `--dry-run`, nothing is changed: no release, fork,
push or PR. Publish still downloads and hashes the artifacts, diffs the
registry manifest against the registry's `main`, and prints the PR it would
open. For a release that does not exist yet, the plan shows the local file's
hash; the real pin comes from the download after the release is created.

Exit codes: 64 usage, 65 invalid data (including a signing key the registry has
not enrolled), 66 missing input (including a missing key directory), 69 tool or
service unavailable (including OpenSSL older than 3.5 and a registry without
author signatures), 70 internal refusal (a push of a non-`publish/` branch, or
signatures that do not verify before the push), 77 not permitted
(`--first-party` for a non-member).

### Author signatures

An author who holds a hybrid key (Ed25519 + ML-DSA-65) signs the artifact
itself, so the registry can show the plugin as Verified under the author's
enrolled key. Two steps, both once per author:

1. **Create the keys.**

   ```bash
   hqplugin keys generate --out ~/.hqplugin/keys
   ```

   This writes `ed25519.pem` and `ml-dsa-65.pem` (private; mode 600 on POSIX,
   and it never overwrites an existing key) and `public-keys.json`, the two raw
   public keys, and prints only the public keys and the file paths. On Windows
   the files inherit the folder's access list; the command prints an `icacls`
   line to restrict it.
2. **Enroll the public keys.** Enrollment is a reviewed PR to the registry
   (`authors/<slug>.json`: your identity, `public_keys` from `public-keys.json`,
   the `id_prefixes` you may sign, and a proof that you hold the Ed25519 key).
   The steps are in the registry's
   [`authors/README.md`](https://github.com/HelloHQ/plugin-registry/blob/main/authors/README.md).

Then publish with the key directory (`--sign-key <dir>` or
`$HQPLUGIN_AUTHOR_KEY_DIR`):

```bash
hqplugin publish --sign-key ~/.hqplugin/keys            # plan, shows the statement
hqplugin publish --sign-key ~/.hqplugin/keys --submit   # opens the PR
```

The PR then also contains `plugins/<id>/author-signatures.json` (an earlier one
is replaced, not merged). Publish signs the *artifact statement*
(`hellohq-plugin-artifact/v1`: id, version, trust tier, execution mode and the
two hashes), not the plugin file, and before pushing it:

- verifies both signatures with the public half of your keys;
- runs the registry's own `scripts/author-signatures.mjs check` on a scratch
  copy of the registry clone, and stops (exit 65) if your key is not an enrolled
  author key whose `id_prefixes` cover the plugin id.

**The trust tier.** The statement includes `trust_tier`, and the registry team
sets `trust_tier: verified` when it merges, not the submitter. So publish signs
the statement of the manifest *as it will be*: the registry manifest with
`trust_tier` forced to `verified`. The manifest in the PR itself keeps
`trust_tier` unset (as for every non-first-party publish, and the PR body says
so), which means the registry's check accepts the signatures only once the tier
is `verified`; the registry's own check on the PR as opened reports that, and
this is expected. If a registry maintainer sets any other tier, the signatures
no longer match and you publish again.

**OpenSSL.** Both algorithms run through the `openssl` command line, so the CLI
adds no crypto library. ML-DSA-65 needs **OpenSSL 3.5 or newer**; Ed25519 only
needs 3.0. `keys generate` and `--sign-key` check with a throwaway ML-DSA key
and exit 69, naming the version they found, when it is missing or too old. Use
`--openssl <path>` or `$HQPLUGIN_OPENSSL` for a specific binary; otherwise
`openssl` on PATH is used. On macOS, Homebrew's `openssl@3` is keg-only, so
`--openssl "$(brew --prefix openssl@3)/bin/openssl"`. The system `openssl` on
macOS is LibreSSL and cannot do ML-DSA. The `openssl` that ships with Git for
Windows may be older than 3.5: check `openssl version`. The keys are the
ordinary PEM files openssl writes, so `node scripts/author-signatures.mjs sign
--ed25519-key ed25519.pem --mldsa-key ml-dsa-65.pem` in the registry and this
CLI sign interchangeably.

**Custody.** The private keys never leave your machine: they are only paths
handed to `openssl`, and the CLI never reads, prints, logs or uploads them (the
PR carries signatures and public keys only). Back both `.pem` files up offline.
They cannot be recovered; losing them means enrolling new keys. If a key
leaks, ask the registry to revoke your enrollment (`"revoked": true` in
`authors/<slug>.json`); apps then stop trusting your certificate on their next
catalog refresh.

The tests for this are `test/author_signing_test.dart` (statement vectors from
the registry, the signer against a fake and a real openssl) and
`test/author_signing_interop_test.dart`, which checks the CLI's signatures with
the registry's scripts and, when `$HQPLUGIN_MLDSA_VERIFY_LIB` points at a built
[mldsa-verify](https://github.com/HelloHQ/mldsa-verify), with the app's
ML-DSA-65 verifier. They use `$HELLOHQ_PLUGIN_REGISTRY_DIR` (a plugin-registry
checkout; `HELLOHQ_REQUIRE_REGISTRY=1` fails instead of skipping) and
`$HQPLUGIN_OPENSSL`; the ML-DSA ones skip without OpenSSL 3.5+ unless
`HQPLUGIN_REQUIRE_OPENSSL_PQ=1`.

`test/publish_network_test.dart` dry-runs against the real released
hello-world; it runs only with `HQPLUGIN_NETWORK_TESTS=1`.

> Distributed via pub.dev and Homebrew when complete.
