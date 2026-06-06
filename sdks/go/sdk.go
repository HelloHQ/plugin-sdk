// Package hellohq is the Tier 2 (Wasm) HelloHQ plugin SDK for Go.
//
// Plugins compile to wasip1 and run in-process in the host via Wasmtime.
// Register a run handler, then export the linear-memory entry point.
//
// Status: skeleton (Phase 4 — begin). The run handler and (de)serialisation are
// real; host imports (portfolio reads) are generated from the WIT and wired in
// a later step.
package hellohq

import "encoding/json"

// ProtocolVersion is the hellohq:plugin version this SDK targets.
const ProtocolVersion = "1.0.0"

// Input is the JSON the host passes to run.
type Input struct {
	Function string                 `json:"function"`
	Args     map[string]interface{} `json:"args"`
}

// RunFunc handles a single invocation. Return JSON bytes conforming to the
// declarative UI schema, or an error.
type RunFunc func(in Input) ([]byte, error)

var registered RunFunc

// Register sets the plugin's run handler. Call it from your package init.
func Register(fn RunFunc) { registered = fn }

// invoke is called by the exported entry point with the raw input bytes.
func invoke(raw []byte) []byte {
	if registered == nil {
		return []byte(`{"error":"no run handler registered"}`)
	}
	var in Input
	if err := json.Unmarshal(raw, &in); err != nil {
		return []byte(`{"error":"invalid input json"}`)
	}
	out, err := registered(in)
	if err != nil {
		b, _ := json.Marshal(map[string]string{"error": err.Error()})
		return b
	}
	return out
}

// hqPluginRun is the linear-memory entry point the host calls.
// Wired with //go:wasmexport once built for wasip1 (Go 1.24+):
//
//	//go:wasmexport hq_plugin_run
//	func hqPluginRun(ptr, length uint32) uint64 { ... }
//
// kept unexported here so the SDK also builds for host-side unit tests.
func hqPluginRun(raw []byte) []byte { return invoke(raw) }
