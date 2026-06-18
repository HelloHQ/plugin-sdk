#!/usr/bin/env bash
# Re-sync the vendored canonical WIT from the plugin-protocol SSOT.
#
#   PLUGIN_PROTOCOL=/path/to/plugin-protocol ./scripts/sync-wit.sh
#
# Defaults to a sibling checkout next to plugin-sdk. Mirrors
# sdks/rust/scripts/sync-wit.sh and sdks/js/scripts/sync-wit.sh.
#
# Only the canonical `hellohq-plugin.wit` is vendored from the SSOT. NOT touched:
#   - wit/component.wit  — SDK-local build worlds (hellohq-plugin-component, the
#                          inference-free identity world; and hellohq-plugin-tinygo,
#                          which also `include`s wasi:cli/imports for TinyGo).
#   - wit/deps/          — WASI-0.2 WIT vendored from TinyGo's lib/wasi-cli so the
#                          TinyGo component encode can resolve its runtime imports.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"           # sdks/go
DEFAULT="$(cd "$HERE/../../.." && pwd)/plugin-protocol"
SRC="${PLUGIN_PROTOCOL:-$DEFAULT}/wit/hellohq-plugin.wit"
DST="$HERE/wit/hellohq-plugin.wit"
[ -f "$SRC" ] || { echo "SSOT not found: $SRC (set PLUGIN_PROTOCOL=...)" >&2; exit 1; }
cp "$SRC" "$DST"
echo "synced $SRC -> $DST"

WASM_TOOLS="$(command -v wasm-tools || echo "$HOME/.cargo/bin/wasm-tools")"
"$WASM_TOOLS" component wit "$HERE/wit/" >/dev/null && echo "vendored wit/ validates"
