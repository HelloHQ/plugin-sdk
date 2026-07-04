# ADR 0001 — Streaming-inference build toolchain: vendor-and-patch, don't fork-to-diverge

- Status: Accepted
- Date: 2026-06-19
- Scope: how the plugin-sdk builds **streaming-inference** plugins (Go + JS)
  while the upstream component-model-async toolchains are unreleased.

## Context

Streaming inference (`inference.complete -> result<stream<string>, api-error>`)
requires a guest that drains a component-model `stream<T>`. The released
toolchains can't do this yet:

- **JS:** `componentize-js 0.21` crashes on `stream` types.
- **Go:** `go.bytecodealliance.org`'s `cm.Stream[T]` has no read API; TinyGo
  can't yield a blocked goroutine to the async executor.

Both gaps are solved on **pre-release work staged in Joel Dice's (`dicej`)
GitHub forks**, which are being upstreamed into the Bytecode Alliance:

| Lang | What's needed (pinned) |
|---|---|
| Go | `dicej/go` `go1.25.5-wasi-on-idle` (release binaries) · `bytecodealliance/wit-bindgen`@`e14b18ca` (Go backend, **upstream**) · preview1 reactor adapter (wasmtime `v39.0.1` release) · `wasm-tools` CLI |
| JS | `dicej/componentize-js`@`b4e73cb3` · `dicej/wasmtime`@`4856b557` · `dicej/wasm-tools`@`54ef27de` + wit-dylib-ffi@`b072b0ca` · `dicej/mozjs`@`e2192ed1` · WASI-SDK 30 · libclang ≥ 19 |

We verified both end-to-end (a Go and a JS plugin drain `inference.complete`'s
stream on a component-model-async host — see `hellohq/docs/55` §6). The JS path
additionally needs one bug fix (see "Patches").

This raised the question: **should HelloHQ fork these projects (with our own
test suites, as our open source) instead of depending on `dicej`'s forks?**

## Decision

**No — do not fork-to-diverge. Vendor-and-patch instead.**

1. **Mirror, don't fork.** Mirror the exact pinned revs into a HelloHQ-controlled
   GitHub org (`mirror-upstream.sh`). This gives reproducibility and immunity to
   upstream force-push/deletion **without** taking ownership of a Go compiler or
   a SpiderMonkey-embedding toolchain.
2. **Patch as overlays, upstream the fixes.** Carry fixes as small patch files
   (`patches/`) applied at image-build time, and send them upstream. No
   maintained fork. Today that is one patch: `pop_record` field-order
   (`patches/0001-pop-record-reverse-field-order.patch`), PR drafted in
   `upstream-pr.md`.
3. **Bake a pinned builder image.** `Dockerfile` produces one image that builds
   **both** Go and JS streaming plugins identically on macOS / Windows / Linux
   (Docker is the cross-OS constant). The heavy `componentize-js` /​ SpiderMonkey
   build happens **once** in the image; authors never compile it.
4. **Consume it from the SDK.** `hqplugin build --inference` and the example
   `build.sh` use this image (pulling sources from the HelloHQ mirror via
   `git config url.<mirror>.insteadOf <upstream>`), so the streaming-build path
   is pinned, reproducible, and OS-consistent.
5. **Gate as experimental; cut over on release.** This path stays behind the
   `--inference` flag. When upstream ships (see Triggers), delete the mirror +
   patch + image and switch to the published `componentize-js` / `wit-bindgen-go`.

## Why not fork-to-diverge

- **`dicej` *is* upstream.** Joel Dice is a core BA maintainer; these forks are
  pre-merge staging headed for StarlingMonkey / mainline Go / wasmtime. The
  trajectory is convergence — a HelloHQ fork would be reconciliation debt.
- **Off-mission maintenance.** "Fork with full test suites" here means owning a
  Go compiler fork and a SpiderMonkey-embedding componentizer (+ wasmtime,
  wasm-tools, mozjs forks). That dwarfs the plugin system and is not our domain.
- **Fragmentation.** These are already Apache-2 OSS. The high-leverage OSS move
  is contributing upstream (shortens the bridge for everyone), not re-forking.

## Trust / security framing

The forks are **build-time only**. The **runtime is official wasmtime** (not a
fork). The build output is a `.wasm` the registry pipeline re-hashes/signs and
the host integrity-checks before execution — so the trust boundary is the
**verified artifact**, not the builder. The pinned mirror + checked-in patch
give a fully auditable, reproducible build for security review.

## Hosting & provenance

Mirrors live in a **dedicated vendor org** (name TBD; `hellohq-vendor` is the
placeholder wired through `mirror-upstream.sh` `MIRROR_ORG` and the Dockerfile
`MIRROR_BASE` — a one-line flip when chosen), **never the product org**. This
keeps first-party code uncluttered and stops these from being misread as HelloHQ
IP (they are upstream Apache-2 / BSD / **MPL-2.0** — `mozjs`/SpiderMonkey is
file-level copyleft — and must stay clearly labeled as third-party).

Match the home to the artifact:

| Host | What | Why |
|---|---|---|
| **Git mirror** (vendor org, private) | componentize-js, wasmtime, wasm-tools, mozjs, wit-bindgen | cargo fetches them as git deps (`insteadOf` / `--git`) |
| **Release assets / object store** | `dicej/go` tarballs, WASI-SDK 30, preview1 adapter | binaries — git is the wrong tool; `mozjs` is multi-GB and may instead be vendored as a source tarball |
| **GHCR** | the built `plugin-builder` image | the **primary artifact authors consume** — mirrors only rebuild + audit it |

Posture:

- **Private** by default — the only consumer is our image build; public mirrors
  would most strongly signal "HelloHQ maintains these." (Go public only if open
  reproducibility becomes a stated goal; the ADR + scripts stay public in
  plugin-sdk regardless.)
- **Write-restricted to the sync identity** (the bot/maintainer running
  `mirror-upstream.sh`); everyone else read-only. An *active* mirror must stay
  writable to sync — so **archive only at deprecation**, not at creation.
- Each repo's description states "pinned mirror of <upstream>@<rev>, not
  maintained, see plugin-sdk ADR-0001."

## Both languages?

Yes. Rust streaming already works on released tools; Go and JS both go through
this image so authors get parity. Go is lighter (the fork ships per-OS release
binaries; only `wit-bindgen` is a `cargo install`); JS bakes the compiled
`componentize-js` into the image so SpiderMonkey is never built by authors.

## Cut-over triggers (delete this toolchain when ANY lands in a release)

- `@bytecodealliance/componentize-js` publishes `stream`/async support → JS uses
  released `jco componentize`.
- `go.bytecodealliance.org/cmd/wit-bindgen-go` emits a readable stream binding
  **and** the "wasi-on-idle" runtime lands in mainline Go → Go drops `dicej/go`.
- Track via the mirror-sync job; when green, this ADR is superseded.

## Consequences

- One Docker dependency for streaming builds (interim). Sync plugins are
  unaffected and keep fast native builds on every OS.
- A periodic `mirror-upstream.sh` sync keeps the mirror current with pinned revs.
- The `pop_record` patch must be re-based if we bump the `componentize-js` rev,
  until it merges upstream.
