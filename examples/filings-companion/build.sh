#!/usr/bin/env bash
# Build the Holdings Filings Companion example.
#
# Tier-1 sidecar: the registry artifact is a single plugin.py, so bundle.py concatenates the
# tested `filings_companion` package and the thin adapter into dist/plugin.py. No UI half
# (ui_type is "declarative"). Prints the sha256 to paste into manifest.json at release time
# (the committed hash is a placeholder).
#
# Requires: Python 3.11+. Run from the example directory.
set -euo pipefail
cd "$(dirname "$0")"

echo ">> [compute] bundle sidecar -> dist/plugin.py"
python3 bundle.py dist/plugin.py
python3 -m py_compile dist/plugin.py
echo "   dist/plugin.py  $(shasum -a 256 dist/plugin.py | cut -d' ' -f1)"
