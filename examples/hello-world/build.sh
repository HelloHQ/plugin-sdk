#!/usr/bin/env bash
# Build Hello World into a HelloHQ Tier-2 component: ./plugin.wasm
#
#   1. compile the cdylib core module for wasm32-unknown-unknown (--locked:
#      the committed Cargo.lock pins every dependency of the released binary)
#   2. `wasm-tools component new` wraps it into a Component Model component
#   3. print the component's WIT (its imports/exports) and its sha256
#
# The output name matches the basename of manifest.json's wasm_url, which is
# what .github/workflows/release-plugin.yml uploads. icon.svg (sidebar_icon)
# is committed, not built.
#
# Requires: rustup target add wasm32-unknown-unknown ; wasm-tools (>=1.252).
set -euo pipefail

cd "$(dirname "$0")"

CORE="target/wasm32-unknown-unknown/release/hello_world.wasm"
OUT="plugin.wasm"

echo ">> cargo build --locked --release --target wasm32-unknown-unknown"
cargo build --locked --release --target wasm32-unknown-unknown

echo ">> wasm-tools component new ${CORE} -> ${OUT}"
# No --adapt / wasi adapter: the guest is no_std with its own dlmalloc, so it
# imports no wasi.
wasm-tools component new "${CORE}" -o "${OUT}"
wasm-tools validate "${OUT}"

echo ">> wasm-tools component wit ${OUT}"
wasm-tools component wit "${OUT}"

if command -v sha256sum >/dev/null 2>&1; then
  sum=$(sha256sum "${OUT}" | cut -d' ' -f1)
else
  sum=$(shasum -a 256 "${OUT}" | cut -d' ' -f1)
fi
echo ">> ${OUT}  sha256=${sum}  bytes=$(wc -c < "${OUT}" | tr -d ' ')"
