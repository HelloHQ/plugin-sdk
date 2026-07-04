#!/usr/bin/env bash
# Build the Go quickstart plugin into a HelloHQ Tier-2 component.
#
#   1. TinyGo compiles main.go to wasip2 and EMBEDS the component-type custom
#      section + adapts the core module into a Component Model component, using
#      the SDK's vendored WIT package and the supplementary
#      `hellohq-plugin-tinygo` world (canonical interfaces minus `inference`).
#   2. `wasi-virt` strips the ambient-capability WASI imports the TinyGo
#      runtime forces into the encode (SA3, doc 59): `wasi:filesystem` is
#      virtualized to an empty deny-all FS and `wasi:cli/environment` to an
#      empty env, so neither appears on the final component's import surface.
#      `wasi:sockets` never appears at all — the build world excludes it.
#   3. `wasm-tools component wit` prints the final imports/exports.
#
# Requires:
#   - TinyGo >= 0.41 (brew tap tinygo-org/tools; brew install tinygo)
#   - wit-bindgen-go (only to (re)generate sdks/go/internal/bindings; bindings
#     are committed, so a plain build does not need it)
#   - wasm-tools >= 1.252 for the final inspection
#   - wasi-virt pinned to the last wasi-0.2.0-compatible rev (TinyGo 0.41 emits
#     wasi@0.2.0 imports; newer wasi-virt revs only support 0.2.1/0.2.3):
#       cargo install --git https://github.com/bytecodealliance/wasi-virt \
#         --rev b662e419bb741c635c7ceceeeae02f5278dd86a4 --locked wasi-virt
set -euo pipefail
cd "$(dirname "$0")"

SDK_WIT="../../sdks/go/wit"
OUT="component_quickstart_go.component.wasm"

WASM_TOOLS="$(command -v wasm-tools || echo "$HOME/.cargo/bin/wasm-tools")"
WASI_VIRT="$(command -v wasi-virt || echo "$HOME/.cargo/bin/wasi-virt")"

echo ">> tinygo build -target=wasip2 (world: hellohq-plugin-tinygo) -> ${OUT}"
# TinyGo's wasip2 target produces a Component Model component directly. The WIT
# package + world tell it which guest exports to emit and which host imports to
# wire. No separate `wasm-tools component new`/adapter step is needed.
#
# We build the `hellohq-plugin-tinygo` world (not the leaner
# `hellohq-plugin-component`): TinyGo's runtime emits WASI-0.2 imports the
# encode must resolve. The world excludes `wasi:sockets` (never imported) but
# must contain `wasi:filesystem` — TinyGo's os.File plumbing survives DCE — so
# the fs surface is removed by the wasi-virt pass below. The hellohq interface
# identities are unchanged.
tinygo build \
  -target=wasip2 \
  -wit-package "${SDK_WIT}" \
  -wit-world hellohq-plugin-tinygo \
  -o "${OUT}" \
  .

echo ">> wasi-virt: deny ambient FS/env (SA3) -> ${OUT}"
# Passthrough only the benign runtime needs (clocks/random/stdio). Everything
# else the component imports is virtualized: wasi:filesystem becomes an empty
# read-only FS (no --mount), wasi:cli/environment an empty env — both vanish
# from the import surface, which CI's SA3 gate asserts.
"${WASI_VIRT}" "${OUT}" \
  --allow-stdio \
  --allow-clocks \
  --allow-random \
  -o "${OUT}.virt" && mv "${OUT}.virt" "${OUT}"

echo ">> component WIT (imports/exports):"
"${WASM_TOOLS}" component wit "${OUT}"

echo ">> component size:"
ls -lh "${OUT}" | awk '{print $5"\t"$9}'
