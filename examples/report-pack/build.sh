#!/usr/bin/env bash
# Build the Report Pack example.
#
# Compute half: the Tier-1 sidecar is ONE file, `plugin.py`, built by bundle.py
# from the SDK's sidecar loop (protocol.py + sidecar.py) and report_pack.py. The
# host runs that single file with `python -I` and nothing installed (the Python
# SDK is not on PyPI), so it must import only the standard library. The
# manifest's `wasm_url` points at it. The output is a pure function of the
# source files, so two builds give identical bytes.
#
# UI half: the framework-agnostic WebView bundle (vanilla TS) -> ui/dist ->
# ui.zip. The UI depends on the in-repo @hellohq/plugin-sdk (file: link), whose
# dist/ is not committed, so it is built first.
#
# ui.zip is reproducible: files are added in a fixed order with a fixed
# timestamp and no extra attributes, so the same sources give the same hash.
#
# Prints the sha256 of both artifacts for manifest.json (content_hash_sha256,
# ui_bundle_hash_sha256); the committed hashes are placeholders until release.
#
# Requires: Node 20+ and npm, Python 3.11+, zip. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

PY="${PYTHON:-python3}"
echo ">> [compute] bundle.py -> plugin.py  ($("${PY}" --version 2>&1))"
"${PY}" bundle.py plugin.py
pyc_dir="$(mktemp -d)"
trap 'rm -rf "${pyc_dir}"' EXIT
PYTHONPYCACHEPREFIX="${pyc_dir}" "${PY}" -m py_compile plugin.py
echo "   plugin.py  $(sha256 plugin.py)"

echo ">> [ui] build the in-repo @hellohq/plugin-sdk (dist/ is not committed)"
( cd ../../sdks/js && npm ci --silent && npm run build --silent )

echo ">> [ui] npm ci + typecheck + build"
( cd ui && rm -rf dist && npm ci --silent && npm run typecheck && npm run build )

echo ">> [ui] zip ui/dist -> ui.zip (reproducible)"
rm -f ui.zip
(
  cd ui/dist
  export TZ=UTC
  find . -type f -exec touch -t 198001010000 {} +
  find . -type f | LC_ALL=C sort | sed 's|^\./||' | zip -q -X -D ../../ui.zip -@
)
echo "   ui.zip     $(sha256 ui.zip)"
