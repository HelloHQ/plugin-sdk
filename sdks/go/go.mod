module github.com/HelloHQ/plugin-sdk/sdks/go

go 1.24

// Tier 2 build target (Component Model + WASI 0.3):
//   tinygo build -target=wasip2 --wit-package wit -o plugin.wasm .
// then wrap/inspect with wasm-tools. See README.md (component workflow).
//
// The legacy core-module path also still works:
//   GOOS=wasip1 GOARCH=wasm go build -o plugin.wasm

require go.bytecodealliance.org/cm v0.3.0
