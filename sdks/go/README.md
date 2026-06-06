# HelloHQ Plugin SDK (Go)

Build Tier 2 (Wasm) HelloHQ plugins in Go.

```bash
GOOS=wasip1 GOARCH=wasm go build -o plugin.wasm
```

```go
package main

import hellohq "github.com/HelloHQ/plugin-sdk/sdks/go"

func init() {
	hellohq.Register(func(in hellohq.Input) ([]byte, error) {
		return []byte(`{"type":"text","content":"Hello from Go"}`), nil
	})
}

func main() {}
```

> **Status:** the run-handler registration and JSON (de)serialisation are real;
> the `//go:wasmexport hq_plugin_run` entry point and host imports are wired in a
> later step. The linear-memory ABI matches the Rust SDK and `PluginWasmService`.

Protocol: https://github.com/HelloHQ/plugin-protocol
