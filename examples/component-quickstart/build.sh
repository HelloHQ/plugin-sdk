#!/usr/bin/env bash
# Build the quickstart plugin into a HelloHQ Tier-2 component.
#
#   1. compile the cdylib core module for wasm32-unknown-unknown
#   2. `wasm-tools component new` wraps it into a Component Model component
#   3. print the component's WIT so you can eyeball imports/exports
#
# Requires: rustup target add wasm32-unknown-unknown ; wasm-tools (>=1.252).
set -euo pipefail

cd "$(dirname "$0")"

CRATE=component_quickstart
CORE="target/wasm32-unknown-unknown/release/${CRATE}.wasm"
OUT="${CRATE}.component.wasm"

echo ">> cargo build --release --target wasm32-unknown-unknown"
cargo build --release --target wasm32-unknown-unknown

echo ">> wasm-tools component new ${CORE} -> ${OUT}"
# No --adapt / wasi adapter: the guest is no_std with its own dlmalloc, so it
# imports no wasi.
wasm-tools component new "${CORE}" -o "${OUT}"

echo ">> wasm-tools component wit ${OUT}"
wasm-tools component wit "${OUT}"
