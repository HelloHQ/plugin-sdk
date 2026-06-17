#!/usr/bin/env bash
# Build the streaming-inference quickstart into a component.
set -euo pipefail
cd "$(dirname "$0")"

CRATE=component_quickstart_inference
CORE="target/wasm32-unknown-unknown/release/${CRATE}.wasm"
OUT="${CRATE}.component.wasm"

echo ">> cargo build --release --target wasm32-unknown-unknown"
cargo build --release --target wasm32-unknown-unknown

echo ">> wasm-tools component new ${CORE} -> ${OUT}"
wasm-tools component new "${CORE}" -o "${OUT}"

echo ">> wasm-tools component wit ${OUT}"
wasm-tools component wit "${OUT}"
