#!/usr/bin/env bash
# Regenerate the wit-bindgen-go bindings under internal/bindings/ from wit/.
#
# Run after ./scripts/sync-wit.sh changes the vendored WIT. The generated code
# is committed, so plugin authors building with TinyGo never need this tool.
#
# Requires: wit-bindgen-go (go install go.bytecodealliance.org/cmd/wit-bindgen-go@latest)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"           # sdks/go
MODULE=github.com/HelloHQ/plugin-sdk/sdks/go

# Resolve a runnable wit-bindgen-go. Prefer the real GOPATH/bin binary over any
# version-manager shim (e.g. goenv shims may not forward to it). On goenv setups
# `go env GOPATH` can be version-scoped while the binary lives under a different
# Go version's bin dir, so fall back to scanning those too.
WBG="$(go env GOPATH)/bin/wit-bindgen-go"
if ! "$WBG" --version >/dev/null 2>&1; then
  WBG="$(command -v wit-bindgen-go || true)"
fi
if ! "$WBG" --version >/dev/null 2>&1; then
  WBG="$(ls -t "$HOME"/go/*/bin/wit-bindgen-go 2>/dev/null | head -1)"
fi
"$WBG" --version >/dev/null 2>&1 || { echo "wit-bindgen-go not runnable; go install go.bytecodealliance.org/cmd/wit-bindgen-go@latest" >&2; exit 1; }

# Generate against the supplementary, inference-free identity world. TinyGo
# builds the wasi-augmented `hellohq-plugin-tinygo` world, but its imports are
# the SAME hellohq interfaces, so the bindings are identical.
rm -rf "$HERE/internal/bindings"
mkdir -p "$HERE/internal/bindings"
"$WBG" generate \
  --world hellohq-plugin-component \
  --out "$HERE/internal/bindings" \
  --package-root "$MODULE/internal/bindings" \
  --cm go.bytecodealliance.org/cm \
  "$HERE/wit/"
echo "regenerated internal/bindings/ from wit/ (world: hellohq-plugin-component)"
