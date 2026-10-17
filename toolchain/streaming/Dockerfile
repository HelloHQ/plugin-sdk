# HelloHQ plugin streaming-build toolchain — one pinned image that builds BOTH
# Go and JS streaming-inference plugins identically on macOS / Windows / Linux.
#
# It vendors the EXACT pinned revs (see docs/adr/0001) from the HelloHQ mirror
# (MIRROR_BASE), rewriting upstream URLs via `git insteadOf` so cargo fetches the
# componentize-js git deps from our mirror. The heavy componentize-js /
# SpiderMonkey build happens ONCE here; authors just `docker run` the result.
#
# Build (after mirror-upstream.sh has populated the mirror):
#   docker build -t ghcr.io/<vendor-org>/plugin-builder:0.1 toolchain/streaming
# (defaults pull from github.com/hellohq-vendor/plugin-mirror-* placeholder;
#  override with --build-arg MIRROR_BASE=https://github.com/<vendor-org>/plugin-mirror)

# MIRROR_BASE points at the dedicated vendor org's mirror-repo prefix.
# `hellohq-vendor` is a placeholder pending the final org name (docs/adr/0001).
# Publish the resulting image to GHCR (ghcr.io/<vendor-org>/plugin-builder) —
# the image is the primary artifact authors consume; these mirrors only rebuild
# and audit it.
ARG MIRROR_BASE=https://github.com/hellohq-vendor/plugin-mirror

# ── Stage 1: build componentize-js (with the pop_record patch) ────────────────
FROM rust:bookworm AS cjs
ARG MIRROR_BASE
ARG CJS_REV=b4e73cb32380da31940ad7f3854538394af37208
ARG WASI_SDK=wasi-sdk-30.0-arm64-linux       # set x86_64-linux for amd64 builders
ENV DEBIAN_FRONTEND=noninteractive
# LLVM 19 from apt.llvm.org's signed repository — NOT by piping the apt.llvm.org
# setup script to a shell, which runs an unpinned script fetched from the
# network (doc 24 SA4). apt verifies the repo signing key and every package
# signature, and the toolchain version is pinned in the source line.
RUN apt-get update -qq && apt-get install -y -qq \
      python3 build-essential clang lld cmake pkg-config curl xz-utils \
      ca-certificates git m4 wget gnupg lsb-release software-properties-common \
 && wget -qO /etc/apt/trusted.gpg.d/llvm-19.asc https://apt.llvm.org/llvm-snapshot.gpg.key \
 && echo "deb http://apt.llvm.org/bookworm/ llvm-toolchain-bookworm-19 main" \
      > /etc/apt/sources.list.d/llvm-19.list \
 && apt-get update -qq && apt-get install -y -qq clang-19 lld-19 libclang-19-dev \
 && rm -rf /var/lib/apt/lists/*
ENV LIBCLANG_PATH=/usr/lib/llvm-19/lib
COPY sha256sums.txt /tmp/sha256sums.txt
RUN rustup target add wasm32-wasip2
# Fetch componentize-js git deps from the HelloHQ mirror, not upstream.
RUN git config --global url."${MIRROR_BASE}-componentize-js".insteadOf https://github.com/dicej/componentize-js \
 && git config --global url."${MIRROR_BASE}-wasmtime".insteadOf       https://github.com/dicej/wasmtime \
 && git config --global url."${MIRROR_BASE}-wasm-tools".insteadOf     https://github.com/dicej/wasm-tools \
 && git config --global url."${MIRROR_BASE}-mozjs".insteadOf          https://github.com/dicej/mozjs
RUN set -eu \
 && curl -fsSL -o "/tmp/${WASI_SDK}.tar.gz" \
      "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-30/${WASI_SDK}.tar.gz" \
 && grep " ${WASI_SDK}.tar.gz$" /tmp/sha256sums.txt | (cd /tmp && sha256sum -c -) \
 && tar xz -C /opt -f "/tmp/${WASI_SDK}.tar.gz" \
 && ln -s "/opt/${WASI_SDK}" /opt/wasi-sdk \
 && rm -f "/tmp/${WASI_SDK}.tar.gz"
ENV WASI_SDK_PATH=/opt/wasi-sdk
WORKDIR /src
RUN git clone "${MIRROR_BASE}-componentize-js" cjs && git -C cjs checkout "${CJS_REV}"
COPY patches/ /patches/
RUN cd cjs && git apply /patches/0001-pop-record-reverse-field-order.patch && cargo build --release
# Persistent global default toolchain (build.rs strips RUSTUP_HOME for the
# nested runtime build — mirror what the spike learned).
RUN cp cjs/target/release/componentize-js /usr/local/bin/componentize-js

# ── Stage 2: Rust tools (wit-bindgen Go backend + wasm-tools) ─────────────────
FROM rust:bookworm AS tools
ARG WIT_BINDGEN_REV=e14b18cae24765882eb2533f2113290244d641b1
RUN cargo install --locked --no-default-features --features go \
      --git https://github.com/bytecodealliance/wit-bindgen --rev "${WIT_BINDGEN_REV}" --root /usr/local \
 && cargo install --locked wasm-tools --version 1.243.0 --root /usr/local

# ── Final: everything to build Go + JS streaming plugins ──────────────────────
FROM debian:bookworm-slim
ARG GO_FORK_TAG=go1.25.5-wasi-on-idle
ARG GO_BOOT=go-linux-arm64-bootstrap          # set go-linux-amd64-bootstrap for amd64
ARG MIRROR_BASE
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY sha256sums.txt /tmp/sha256sums.txt
COPY --from=cjs   /usr/local/bin/componentize-js /usr/local/bin/componentize-js
COPY --from=tools /usr/local/bin/wit-bindgen     /usr/local/bin/wit-bindgen
COPY --from=tools /usr/local/bin/wasm-tools       /usr/local/bin/wasm-tools
# Go fork (wasi-on-idle) from the HelloHQ mirror release. The pin is the
# upstream release bytes; a mirror that serves different bytes fails the build.
RUN set -eu \
 && curl -fsSL -o "/tmp/${GO_BOOT}.tbz" \
      "${MIRROR_BASE}-go/releases/download/${GO_FORK_TAG}/${GO_BOOT}.tbz" \
 && grep " ${GO_BOOT}.tbz$" /tmp/sha256sums.txt | (cd /tmp && sha256sum -c -) \
 && tar xj -C /opt -f "/tmp/${GO_BOOT}.tbz" \
 && rm -f "/tmp/${GO_BOOT}.tbz"
ENV HQ_GO_WASI_ON_IDLE=/opt/${GO_BOOT}/bin/go
# preview1 reactor adapter.
RUN set -eu \
 && mkdir -p /opt/adapter \
 && curl -fsSL \
      https://github.com/bytecodealliance/wasmtime/releases/download/v39.0.1/wasi_snapshot_preview1.reactor.wasm \
      -o /opt/adapter/wasi_snapshot_preview1.reactor.wasm \
 && grep " wasi_snapshot_preview1.reactor.wasm$" /tmp/sha256sums.txt | (cd /opt/adapter && sha256sum -c -)
ENV HQ_WASI_ADAPTER=/opt/adapter/wasi_snapshot_preview1.reactor.wasm
WORKDIR /plugin
# `hqplugin build --inference --docker` mounts the plugin at /plugin and runs
# the Go/JS pipeline using the tools + env above.
