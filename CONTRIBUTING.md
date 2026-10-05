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

## Releasing an example plugin

Example plugins that the public registry lists (today: `examples/hello-world`)
are released by `.github/workflows/release-plugin.yml`, which builds them in CI
from the tagged source. Nothing is built or uploaded from a laptop.

1. In `examples/<dir>/manifest.json`, set `version` to the new version and point
   every release URL at the new tag:
   `wasm_url`, and `ui_bundle_url` / `sidebar_icon` when present, must each be
   exactly `https://github.com/HelloHQ/plugin-sdk/releases/download/<dir>-v<version>/<file>`.
   (A WebView plugin's `sidebar_icon` may instead be a path inside its UI
   bundle.) `examples/<dir>/build.sh` must produce each `<file>` in
   `examples/<dir>/`, or it must be committed there.
2. Merge that to `main`, then tag the merge commit and push the tag:

   ```sh
   git tag hello-world-v1.0.0 <merge-commit>
   git push origin hello-world-v1.0.0
   ```

   The tag is `<example-dir>-v<MAJOR.MINOR.PATCH>`, optionally with a
   `-<prerelease>` suffix (published as a prerelease).
3. The workflow checks the tag and manifest (`scripts/release_plugin_check.py`;
   run `python3 scripts/release_plugin_check.py check --tag <tag>` first to
   catch mistakes), runs `build.sh`, and creates the release
   "`<dir>` v`<version>`" with the assets, a `SHA256SUMS` file, a build
   provenance attestation for each asset, and notes listing each asset's
   SHA-256 and size, the toolchain versions and the source commit. It is never
   marked as the repository's latest release.
4. Pin the release in
   [HelloHQ/plugin-registry](https://github.com/HelloHQ/plugin-registry) from
   `examples/<dir>`:

   ```sh
   dart run ../../cli/bin/hqplugin.dart publish --first-party           # plan
   dart run ../../cli/bin/hqplugin.dart publish --first-party --submit  # PR
   ```

   `hqplugin publish` downloads the released files from the manifest's URLs and
   pins their SHA-256 (and `ui_bundle_hash_sha256` for a WebView plugin) in the
   registry's copy of the manifest. Do not pass `--release`: the release
   workflow made the release. `--first-party` is needed because the examples
   are `provenance: core`; it is checked against HelloHQ org membership. The
   copy in this repo keeps the all-zero placeholder hash.

Releases are immutable because registry manifests pin their hashes: the
workflow fails if a release (or draft) for the tag already exists and never
replaces assets. To fix a release, bump the version and tag again. Verify an
asset with `gh attestation verify <file> --repo HelloHQ/plugin-sdk`.

Tests for the release check: `python3 -m unittest discover -s scripts -p 'test_*.py'`.
