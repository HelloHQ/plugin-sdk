#!/usr/bin/env bash
# Build the JS quickstart plugin into a HelloHQ Tier-2 component.
#
#   1. esbuild bundles src/plugin.ts -> dist/plugin.bundle.js, keeping the host
#      virtual imports (hellohq:* / wasi:*) EXTERNAL — jco's StarlingMonkey
#      loader resolves only the single entry module plus those virtuals, so the
#      SDK must be inlined into one file first.
#   2. `jco componentize` wraps the bundle into a Component Model component
#      against the SDK's vendored WIT, building the `hellohq-plugin-component`
#      world (canonical interfaces minus `inference` — see the SDK wit/README).
#   3. `wasm-tools component wit` prints the component's imports/exports.
#
# Requires: npm install (jco + esbuild as devDeps); wasm-tools (>=1.252) for the
# final inspection (jco's bundled `jco wit` works too).
set -euo pipefail
cd "$(dirname "$0")"

SDK_WIT="../../sdks/js/wit"
ENTRY="src/plugin.ts"
BUNDLE="dist/plugin.bundle.js"
OUT="component_quickstart_js.component.wasm"

mkdir -p dist

echo ">> esbuild bundle ${ENTRY} -> ${BUNDLE}"
# `--packages=bundle` inlines @hellohq/plugin-sdk; the host virtual modules stay
# external so jco wires them to the component's imports.
npx esbuild "${ENTRY}" \
  --bundle \
  --format=esm \
  --platform=neutral \
  --target=es2022 \
  --external:'hellohq:*' \
  --external:'wasi:*' \
  --outfile="${BUNDLE}"

echo ">> jco componentize ${BUNDLE} -> ${OUT} (world: hellohq-plugin-component)"
npx jco componentize "${BUNDLE}" \
  --wit "${SDK_WIT}" \
  --world-name hellohq-plugin-component \
  -o "${OUT}"

echo ">> component WIT (imports/exports):"
# Prefer the standalone wasm-tools if present; jco bundles an equivalent.
if command -v wasm-tools >/dev/null 2>&1; then
  wasm-tools component wit "${OUT}"
elif [ -x "$HOME/.cargo/bin/wasm-tools" ]; then
  "$HOME/.cargo/bin/wasm-tools" component wit "${OUT}"
else
  npx jco wit "${OUT}"
fi
