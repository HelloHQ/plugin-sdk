#!/usr/bin/env bash
# Build the macro-context sidecar: bundle package + entry point into ONE file
# (dist/plugin.py), byte-compile it, and print its sha256 for manifest.json.
# The committed manifest carries a placeholder hash. Run from this directory.
set -euo pipefail
cd "$(dirname "$0")"
python3 bundle.py dist/plugin.py
python3 -m py_compile dist/plugin.py
echo "   dist/plugin.py  $(shasum -a 256 dist/plugin.py | cut -d' ' -f1)"
