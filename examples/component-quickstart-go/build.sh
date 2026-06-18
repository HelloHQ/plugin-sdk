#!/usr/bin/env bash
# Build the Go quickstart plugin into a HelloHQ Tier-2 component.
#
#   1. TinyGo compiles main.go to wasip2 and EMBEDS the component-type custom
#      section + adapts the core module into a Component Model component, using
#      the SDK's vendored WIT package and the supplementary
#      `hellohq-plugin-component` world (canonical interfaces minus `inference`).
#   2. `wasm-tools component wit` prints the component's imports/exports.
#
# Requires:
#   - TinyGo >= 0.41 (brew tap tinygo-org/tools; brew install tinygo)
#   - wit-bindgen-go (only to (re)generate sdks/go/internal/bindings; bindings
#     are committed, so a plain build does not need it)
#   - wasm-tools >= 1.252 for the final inspection
set -euo pipefail
cd "$(dirname "$0")"

SDK_WIT="../../sdks/go/wit"
OUT="component_quickstart_go.component.wasm"

WASM_TOOLS="$(command -v wasm-tools || echo "$HOME/.cargo/bin/wasm-tools")"

echo ">> tinygo build -target=wasip2 (world: hellohq-plugin-tinygo) -> ${OUT}"
# TinyGo's wasip2 target produces a Component Model component directly. The WIT
# package + world tell it which guest exports to emit and which host imports to
# wire. No separate `wasm-tools component new`/adapter step is needed.
#
# We build the `hellohq-plugin-tinygo` world (not the leaner
# `hellohq-plugin-component`): TinyGo's runtime emits WASI-0.2 imports, so the
# build world must `include wasi:cli/imports@0.2.0` for the component encode to
# resolve them. The hellohq interface identities are unchanged.
tinygo build \
  -target=wasip2 \
  -wit-package "${SDK_WIT}" \
  -wit-world hellohq-plugin-tinygo \
  -o "${OUT}" \
  .

echo ">> component WIT (imports/exports):"
"${WASM_TOOLS}" component wit "${OUT}"

echo ">> component size:"
ls -lh "${OUT}" | awk '{print $5"\t"$9}'
