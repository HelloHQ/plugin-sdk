#!/usr/bin/env bash
# Create/sync HelloHQ GitHub mirrors of the pinned streaming-toolchain sources.
#
# This is the "vendor, don't fork" step from docs/adr/0001: we mirror the EXACT
# pinned revs into a HelloHQ-controlled org so builds are reproducible and immune
# to upstream force-push/deletion — WITHOUT taking ownership of these projects.
#
# Requires: gh (authenticated with repo-create rights on $MIRROR_ORG), git.
# Run once to create, re-run to sync. mozjs + go are large (SpiderMonkey / a Go
# distribution) — expect a multi-GB one-time push.
set -euo pipefail

# The dedicated vendor org (NOT the product org). Set MIRROR_ORG to the chosen
# name before running — `hellohq-vendor` is a placeholder pending the final name
# (see docs/adr/0001 "Hosting & provenance"). Mirrors are PRIVATE by default
# (we don't claim ownership of upstream); write access should be restricted to
# the sync identity running this script. Repos are archived only at DEPRECATION,
# not here (an active mirror must stay writable to sync).
MIRROR_ORG="${MIRROR_ORG:-hellohq-vendor}"
VISIBILITY="${MIRROR_VISIBILITY:-private}"   # private (recommended) or public
WORK="${WORK:-$(mktemp -d)}"

# upstream-url|mirror-repo|pin (commit or tag to guarantee is present)
INVENTORY=(
  "https://github.com/dicej/componentize-js|plugin-mirror-componentize-js|b4e73cb32380da31940ad7f3854538394af37208"
  "https://github.com/dicej/wasmtime|plugin-mirror-wasmtime|4856b557"
  "https://github.com/dicej/wasm-tools|plugin-mirror-wasm-tools|54ef27de"
  "https://github.com/dicej/mozjs|plugin-mirror-mozjs|e2192ed1"
  "https://github.com/bytecodealliance/wit-bindgen|plugin-mirror-wit-bindgen|e14b18ca"
)

# The Go compiler fork ships per-OS RELEASE BINARIES, so we mirror the release
# assets rather than the (huge) compiler source. Handled separately below.
GO_FORK_REPO="dicej/go"
GO_FORK_TAG="go1.25.5-wasi-on-idle"

mirror_repo() {
  local url="$1" name="$2" pin="$3"
  local full="$MIRROR_ORG/$name"
  echo ">> mirror $url  ->  $full  (pin $pin)"
  if ! gh repo view "$full" >/dev/null 2>&1; then
    gh repo create "$full" "--$VISIBILITY" \
      --description "Pinned mirror of $url (plugin-sdk streaming toolchain; see plugin-sdk/docs/adr/0001)"
  fi
  local dir="$WORK/$name.git"
  if [ ! -d "$dir" ]; then git clone --mirror "$url" "$dir"; else git -C "$dir" remote update --prune; fi
  git -C "$dir" push --mirror "https://github.com/$full.git"
  # Guarantee the exact pinned rev is retained even if upstream prunes it.
  git -C "$dir" tag -f "pinned/$pin" "$pin" 2>/dev/null || true
  git -C "$dir" push -f "https://github.com/$full.git" "pinned/$pin" 2>/dev/null || true
}

for row in "${INVENTORY[@]}"; do
  IFS='|' read -r url name pin <<<"$row"
  mirror_repo "$url" "$name" "$pin"
done

# Go fork: re-host the release tarballs under a HelloHQ release.
echo ">> mirror $GO_FORK_REPO release $GO_FORK_TAG -> $MIRROR_ORG/plugin-mirror-go"
GO_MIRROR="$MIRROR_ORG/plugin-mirror-go"
gh repo view "$GO_MIRROR" >/dev/null 2>&1 || gh repo create "$GO_MIRROR" "--$VISIBILITY" \
  --description "Pinned mirror of $GO_FORK_REPO release assets ($GO_FORK_TAG)"
mkdir -p "$WORK/go-assets" && (cd "$WORK/go-assets" && \
  gh release download "$GO_FORK_TAG" --repo "$GO_FORK_REPO" --clobber)
gh release view "$GO_FORK_TAG" --repo "$GO_MIRROR" >/dev/null 2>&1 || \
  gh release create "$GO_FORK_TAG" --repo "$GO_MIRROR" --title "$GO_FORK_TAG (mirror)" \
    --notes "Mirror of $GO_FORK_REPO@$GO_FORK_TAG. See plugin-sdk/docs/adr/0001." \
    "$WORK"/go-assets/*

echo ">> done. Mirrors under https://github.com/$MIRROR_ORG/plugin-mirror-*"
echo ">> WASI-SDK 30 + the preview1 adapter are upstream RELEASES; mirror similarly if desired."
