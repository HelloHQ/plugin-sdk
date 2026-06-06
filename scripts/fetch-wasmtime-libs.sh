#!/usr/bin/env bash
# fetch-wasmtime-libs.sh
#
# Provisions the native Wasmtime C-API shared library used by the Tier-2 plugin
# runtime (lib/app/utils/ffi/wasmtime_loader.dart), for the CURRENT host OS,
# into third_party/wasmtime/<platform>/.
#
#   macOS arm64  → third_party/wasmtime/macos-arm64/libwasmtime.dylib
#   macOS x64    → third_party/wasmtime/macos-x64/libwasmtime.dylib
#   Linux x64    → third_party/wasmtime/linux-x64/libwasmtime.so
#   Linux arm64  → third_party/wasmtime/linux-arm64/libwasmtime.so
#   Windows x64  → third_party/wasmtime/windows-x64/wasmtime.dll
#
# The headers (third_party/wasmtime/include/) are committed so `dart run ffigen`
# works without a fetch. Only the large platform binaries are provisioned here
# and git-ignored. Run on every OS (Windows runners call bash).
#
# macOS arm64/x64, Linux x64/arm64, and Windows x64 are all wired into the
# loader. macOS/Linux release archives are .tar.xz; Windows is .zip — handled
# per-OS below.
#
# Pinned to WASMTIME_VERSION, which must match the ffigen bindings
# (lib/app/utils/ffi/wasmtime_bindings.dart). Idempotent: existing target is a
# no-op unless FORCE=1.
set -euo pipefail

WASMTIME_VERSION="${WASMTIME_VERSION:-v45.0.1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_BASE="$ROOT/third_party/wasmtime"
WORK=".ci-wasmtime-tmp"

rm -rf "$WORK"; mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

# $1 = release asset infix, $2 = platform dir, $3 = lib filename, $4 = ext (tar.xz|zip)
fetch() {
  local infix="$1" platdir="$2" libname="$3" ext="$4"
  local dest="$DEST_BASE/$platdir/$libname"

  if [[ -f "$dest" && "${FORCE:-0}" != "1" ]]; then
    echo "✓ $dest already present — skipping (set FORCE=1 to re-fetch)."
    return 0
  fi

  local asset="wasmtime-${WASMTIME_VERSION}-${infix}-c-api"
  local url="https://github.com/bytecodealliance/wasmtime/releases/download/${WASMTIME_VERSION}/${asset}.${ext}"
  echo "▸ wasmtime: $url"
  curl -fsSL "$url" -o "$WORK/capi.${ext}"

  if [[ "$ext" == "zip" ]]; then
    if command -v unzip >/dev/null 2>&1; then
      unzip -oq "$WORK/capi.zip" -d "$WORK"
    else
      tar -xf "$WORK/capi.zip" -C "$WORK"   # bsdtar reads zip on Win/macOS
    fi
  else
    tar -xf "$WORK/capi.${ext}" -C "$WORK"
  fi

  local extracted="$WORK/${asset}"
  mkdir -p "$DEST_BASE/$platdir"
  cp "$extracted/lib/$libname" "$dest"
  # Refresh committed headers from the same release for ffigen parity.
  rm -rf "$DEST_BASE/include"
  cp -R "$extracted/include" "$DEST_BASE/include"
  echo "${WASMTIME_VERSION}" > "$DEST_BASE/VERSION"
  echo "  → $dest (${WASMTIME_VERSION})"
}

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  fetch "aarch64-macos"  "macos-arm64" "libwasmtime.dylib" "tar.xz" ;;
  Darwin-x86_64) fetch "x86_64-macos"   "macos-x64"   "libwasmtime.dylib" "tar.xz" ;;
  Linux-x86_64)  fetch "x86_64-linux"   "linux-x64"   "libwasmtime.so"    "tar.xz" ;;
  Linux-aarch64) fetch "aarch64-linux"  "linux-arm64" "libwasmtime.so"    "tar.xz" ;;
  # Windows runners invoke bash via Git Bash / MSYS / Cygwin. C-API ships as .zip.
  MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
                 fetch "x86_64-windows" "windows-x64" "wasmtime.dll"      "zip" ;;
  *)
    echo "No Wasmtime C-API mapping for $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

echo "✅ Wasmtime native lib provisioned."
