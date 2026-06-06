//go:build !wasip1

// Stub implementations so the SDK links for native host-side unit tests.
// These mirror the pattern used by the Rust SDK (#[cfg(not(target_arch = "wasm32"))]).

package hellohq

import "encoding/json"

func hqReadImpl(_ []byte) []byte {
	// Return a well-formed error response rather than panicking so callers can
	// test error-path behaviour without a real host.
	resp, _ := json.Marshal(map[string]any{
		"ok":    false,
		"error": "hq_read is only available inside the Wasm host",
	})
	return resp
}

func emitEventImpl(_ string, _ []byte) {}
