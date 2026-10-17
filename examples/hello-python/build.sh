#!/usr/bin/env bash
# Build Hello Python into its Tier-1 sidecar artifact: ./plugin.py
#
#   1. bundle.py joins the SDK's NDJSON sidecar loop
#      (sdks/python/hellohq_plugin_sdk/{protocol,sidecar}.py) and
#      hello_python.py into ONE standard-library-only file. The host runs that
#      single file with nothing installed (the SDK is not on PyPI).
#   2. py_compile checks it compiles (the .pyc goes to a scratch
#      prefix, not next to the artifact).
#   3. print its sha256 and size.
#
# The output is a pure function of the three source files (no timestamps, no
# paths), so two builds produce identical bytes. Its name matches the basename
# of manifest.json's wasm_url, which is what .github/workflows/release-plugin.yml
# uploads. icon.svg (sidebar_icon) is committed, not built.
#
# Requires: Python 3.11+. Run from anywhere.
set -euo pipefail

cd "$(dirname "$0")"

OUT="plugin.py"
# python3, or `python` where only that exists (Windows runners); override with PYTHON=.
PY="${PYTHON:-}"
if [ -z "${PY}" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

echo ">> bundle.py -> ${OUT}  ($("${PY}" --version 2>&1))"
"${PY}" bundle.py "${OUT}"

echo ">> py_compile ${OUT}"
pyc_dir="$(mktemp -d)"
trap 'rm -rf "${pyc_dir}"' EXIT
PYTHONPYCACHEPREFIX="${pyc_dir}" "${PY}" -m py_compile "${OUT}"

if command -v sha256sum >/dev/null 2>&1; then
  sum=$(sha256sum "${OUT}" | cut -d' ' -f1)
else
  sum=$(shasum -a 256 "${OUT}" | cut -d' ' -f1)
fi
echo ">> ${OUT}  sha256=${sum}  bytes=$(wc -c < "${OUT}" | tr -d ' ')"
