#!/usr/bin/env bash
# Re-sync the vendored canonical WIT from the plugin-protocol SSOT.
#
#   PLUGIN_PROTOCOL=/path/to/plugin-protocol ./scripts/sync-wit.sh
#
# Defaults to a sibling checkout next to plugin-sdk.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"           # sdks/rust
DEFAULT="$(cd "$HERE/../../.." && pwd)/plugin-protocol"
SRC="${PLUGIN_PROTOCOL:-$DEFAULT}/wit/hellohq-plugin.wit"
DST="$HERE/wit/hellohq-plugin.wit"
[ -f "$SRC" ] || { echo "SSOT not found: $SRC (set PLUGIN_PROTOCOL=...)" >&2; exit 1; }
cp "$SRC" "$DST"
echo "synced $SRC -> $DST"
wasm-tools component wit "$HERE/wit/" >/dev/null && echo "vendored wit/ validates"
