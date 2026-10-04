#!/usr/bin/env bash
# Build the Report Pack example.
#
# Compute half: the Tier-1 sidecar is the Python script `plugin.py` itself —
# no compile step. The host runs it via uv / python3 (network-jailed). The
# manifest's `wasm_url` points at `plugin.py` (the sidecar artifact), so the
# sidecar must stay a single self-contained file (it imports only the stdlib
# and hellohq_plugin_sdk).
#
# UI half: the framework-agnostic WebView bundle (vanilla TS) -> ui/dist ->
# ui.zip. The UI depends on the in-repo @hellohq/plugin-sdk (file: link), whose
# dist/ is not committed, so it is built first.
#
# Requires: Node + npm, Python 3. Run from the example directory.
set -euo pipefail
cd "$(dirname "$0")"

echo ">> [compute] sidecar = plugin.py (no build step)"
python3 -m py_compile plugin.py
echo "   plugin.py  $(shasum -a 256 plugin.py | cut -d' ' -f1)"

echo ">> [ui] build the in-repo @hellohq/plugin-sdk (dist/ is not committed)"
( cd ../../sdks/js && npm ci --silent && npm run build --silent )

echo ">> [ui] npm install + typecheck + build"
( cd ui && npm install --silent && npm run typecheck && npm run build )

echo ">> [ui] zip ui/dist -> ui.zip"
rm -f ui.zip
( cd ui/dist && zip -qr ../../ui.zip . )
echo "   ui.zip     $(shasum -a 256 ui.zip | cut -d' ' -f1)"
