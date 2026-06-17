#!/usr/bin/env bash
# Build the Notes Keeper example.
#
# Compute half: the Tier-1 sidecar is the Python script `plugin.py` itself —
# no compile step. The host runs it via uv / python3 (network-jailed). The
# manifest's `wasm_url` points at `plugin.py` (the sidecar artifact).
#
# UI half: the framework-agnostic WebView bundle (vanilla TS) -> ui/dist ->
# ui.zip (esbuild bundles src/main.ts + the @hellohq/plugin-sdk HQHost wrapper;
# index.html + styles.css copied alongside).
#
# Requires: Node + npm. Run from the example directory.
set -euo pipefail
cd "$(dirname "$0")"

echo ">> [compute] sidecar = plugin.py (no build step)"
python3 -m py_compile plugin.py
echo "   plugin.py  $(shasum -a 256 plugin.py | cut -d' ' -f1)"

echo ">> [ui] npm install + build"
( cd ui && npm install --silent && npm run build )

echo ">> [ui] zip ui/dist -> ui.zip"
rm -f ui.zip
( cd ui/dist && zip -qr ../../ui.zip . )
echo "   ui.zip     $(shasum -a 256 ui.zip | cut -d' ' -f1)"
