#!/usr/bin/env bash
# Build the Go STREAMING-INFERENCE quickstart into a HelloHQ Tier-2 component.
#
# Unlike the sync Go quickstart (TinyGo + wit-bindgen-go), draining
# `inference.complete`'s `stream<string>` needs a readable stream binding and a
# goroutine that yields while the host produces tokens. That is NOT available in
# `go.bytecodealliance.org/cm` (its `cm.Stream[T]` has no read API) nor in
# TinyGo. It requires three currently-UNRELEASED pieces (pinned git revs + a Go
# fork), fetched on demand into ./.toolchain (gitignored):
#
#   1. bytecodealliance/wit-bindgen Go backend  — emits readable StreamReader[T]
#      + goroutine-based concurrency on the Component Model concurrency ABI.
#   2. dicej/go "wasi-on-idle" fork             — a blocking goroutine yields to
#      the component-model async executor instead of dead-locking.
#   3. wasi_snapshot_preview1.reactor adapter   — wraps the wasip1 core module
#      into a Component Model component.
#
# The generated bindings under ./ (wit_bindings.go, wit_types/, wit_async/, …)
# are COMMITTED, so a plain build only needs the Go fork + the adapter; pass
# `--gen` to regenerate them (needs wit-bindgen). When these tools ship in
# releases this script collapses to roughly the size of the sync one.
#
# Requires: curl, tar, cargo (only for --gen), wasm-tools >= 1.252.
set -euo pipefail
cd "$(dirname "$0")"

SDK_WIT="../../../sdks/go/wit"
WORLD="hellohq-plugin-inference"
OUT="component_quickstart_go_inference.component.wasm"
TC=".toolchain"

GO_FORK_TAG="go1.25.5-wasi-on-idle"
WIT_BINDGEN_REV="e14b18ca"
ADAPTER_VER="v39.0.1"

WASM_TOOLS="$(command -v wasm-tools || echo "$HOME/.cargo/bin/wasm-tools")"
mkdir -p "$TC"

# SA4 (doc 59): verify a downloaded toolchain artifact against a pinned SHA-256
# before using it, so a compromised/MITM'd release asset can't inject a
# backdoored compiler/runtime into the build. Expected hashes are supplied out
# of band (env or CI secret) because they are release-specific; when a pin is
# provided a mismatch is fatal, and when it is absent we emit a LOUD warning
# rather than silently trusting the download. Populate GO_FORK_SHA256 /
# ADAPTER_SHA256 (see toolchain/streaming for the mirror-pinning story) to make
# these hard gates. `cargo install --locked --git --rev <sha>` below is already
# SHA-pinned.
verify_sha256() {
  # $1 = file, $2 = expected hex sha256 (may be empty = unpinned)
  local file="$1" expected="$2" got
  if command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$file" | cut -d' ' -f1)
  else got=$(shasum -a 256 "$file" | cut -d' ' -f1); fi
  if [ -z "$expected" ]; then
    echo "::warning::SA4: $file downloaded WITHOUT a pinned sha256 (got $got). Set its *_SHA256 pin to make this a hard integrity gate."
    return 0
  fi
  if [ "$got" != "$expected" ]; then
    echo "::error::SA4: sha256 mismatch for $file — expected $expected got $got. Refusing to use a tampered toolchain artifact." >&2
    exit 1
  fi
  echo ">> verified sha256 $file"
}

# 1. Patched Go fork (wasi-on-idle scheduler).
GO_BOOT="go-$(uname -s | tr 'A-Z' 'a-z')-$(uname -m | sed s/aarch64/arm64/)-bootstrap"
GO="$TC/$GO_BOOT/bin/go"
if [ ! -x "$GO" ]; then
  echo ">> fetching Go fork $GO_FORK_TAG ($GO_BOOT)"
  curl -sL "https://github.com/dicej/go/releases/download/$GO_FORK_TAG/$GO_BOOT.tbz" \
    -o "$TC/$GO_BOOT.tbz"
  verify_sha256 "$TC/$GO_BOOT.tbz" "${GO_FORK_SHA256:-}"
  tar xf "$TC/$GO_BOOT.tbz" -C "$TC"
fi

# 2. preview1 reactor adapter.
ADAPTER="$TC/wasi_snapshot_preview1.reactor.wasm"
if [ ! -f "$ADAPTER" ]; then
  echo ">> fetching preview1 reactor adapter $ADAPTER_VER"
  curl -sL "https://github.com/bytecodealliance/wasmtime/releases/download/$ADAPTER_VER/wasi_snapshot_preview1.reactor.wasm" \
    -o "$ADAPTER"
  verify_sha256 "$ADAPTER" "${ADAPTER_SHA256:-}"
fi

# 3. (optional) regenerate the committed bindings with the wit-bindgen Go backend.
if [ "${1:-}" = "--gen" ]; then
  WB="$TC/bin/wit-bindgen"
  if [ ! -x "$WB" ]; then
    echo ">> installing wit-bindgen (rev $WIT_BINDGEN_REV, --features go)"
    cargo install --locked --no-default-features --features go \
      --git https://github.com/bytecodealliance/wit-bindgen --rev "$WIT_BINDGEN_REV" --root "$TC"
  fi
  # Preserve the hand-written guest impl; regenerate everything else.
  cp export_wit_world/run.go "$TC/run.go.keep"
  "$WB" go --world "$WORLD" --out-dir . "$SDK_WIT"
  cp "$TC/run.go.keep" export_wit_world/run.go
  echo ">> regenerated bindings (world: $WORLD)"
fi

# 4. Compile the wasip1 core module with the Go fork.
echo ">> go build (wasip1, c-shared) -> core module"
GOOS=wasip1 GOARCH=wasm "$GO" build -o "$TC/core.wasm" -buildmode=c-shared -ldflags=-checklinkname=0 .

# 5. Embed the WIT + adapt into a component.
echo ">> wasm-tools component embed + new (world: $WORLD) -> $OUT"
"$WASM_TOOLS" component embed "$SDK_WIT" --world "$WORLD" "$TC/core.wasm" --output "$TC/with-wit.wasm"
"$WASM_TOOLS" component new --adapt "$ADAPTER" "$TC/with-wit.wasm" --output "$OUT"

echo ">> component WIT (imports/exports):"
"$WASM_TOOLS" component wit "$OUT"

echo ">> component size:"
ls -lh "$OUT" | awk '{print $5"\t"$9}'
