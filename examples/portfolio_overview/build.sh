#!/usr/bin/env bash
# Build BOTH halves of the Portfolio Overview WebView example:
#
#   1. compute: the Rust SDK plugin -> wasm32-unknown-unknown core module ->
#      `wasm-tools component new` -> portfolio_overview.component.wasm
#      (a `hellohq:plugin@0.1.0` component; the host's Tier-2 executor runs it).
#   2. ui: the framework-agnostic WebView bundle -> ui/dist -> ui.zip
#      (esbuild bundles src/main.ts + the @hellohq/plugin-sdk HQHost wrapper;
#       index.html + styles.css are copied alongside).
#
# The manifest (manifest.json) points at both artifacts (wasm_url +
# ui_bundle_url) with their sha256 hashes.
#
# Requires: the wasm32-unknown-unknown target + wasm-tools (compute); Node + npm
# (ui). Run from the example directory.
set -euo pipefail
cd "$(dirname "$0")"

CORE="target/wasm32-unknown-unknown/release/portfolio_overview.wasm"
COMPONENT="portfolio_overview.component.wasm"

echo ">> [compute] cargo build (wasm32-unknown-unknown, release)"
cargo build --release --target wasm32-unknown-unknown

echo ">> [compute] wasm-tools component new -> ${COMPONENT}"
wasm-tools component new "${CORE}" -o "${COMPONENT}"
wasm-tools component wit "${COMPONENT}"

echo ">> [ui] npm install + build"
( cd ui && npm install --silent && npm run build )

echo ">> [ui] zip ui/dist -> ui.zip"
rm -f ui.zip
( cd ui/dist && zip -qr ../../ui.zip . )

echo ">> artifacts:"
echo "   ${COMPONENT}  $(shasum -a 256 "${COMPONENT}" | cut -d' ' -f1)"
echo "   ui.zip                          $(shasum -a 256 ui.zip | cut -d' ' -f1)"
