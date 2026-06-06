//go:build wasip1

package hellohq

import "unsafe"

// Host function imports from the "env" module — wired by the Wasmtime host.
//
//go:wasmimport env hq_read
func hqReadHost(reqPtr uint32, reqLen uint32) uint64

//go:wasmimport env emit_event
func emitEventHost(namePtr uint32, nameLen uint32, payloadPtr uint32, payloadLen uint32)

// allocPool keeps references alive so the GC never reclaims memory the host
// is still reading. Go's Wasm runtime uses a non-moving GC, so addresses are
// stable; the pool just prevents collection.
var allocPool [][]byte

// alloc is exported to the host so it can request guest memory for responses.
//
//go:wasmexport alloc
func alloc(size uint32) uint32 {
	if size == 0 {
		return 0
	}
	buf := make([]byte, size)
	allocPool = append(allocPool, buf)
	return uint32(uintptr(unsafe.Pointer(&buf[0])))
}

// run is the main entry point called once per user invocation.
//
//go:wasmexport run
func run(ptr uint32, length uint32) uint64 {
	var input []byte
	if ptr != 0 && length > 0 {
		input = unsafe.Slice((*byte)(unsafe.Pointer(uintptr(ptr))), length)
	}
	out := invoke(input)
	outPtr := allocBytes(out)
	return (uint64(outPtr) << 32) | uint64(len(out))
}

func allocBytes(data []byte) uint32 {
	if len(data) == 0 {
		return 0
	}
	ptr := alloc(uint32(len(data)))
	copy(unsafe.Slice((*byte)(unsafe.Pointer(uintptr(ptr))), len(data)), data)
	return ptr
}

// hqReadImpl sends a JSON request to the host and returns the response bytes.
func hqReadImpl(req []byte) []byte {
	reqPtr := allocBytes(req)
	packed := hqReadHost(reqPtr, uint32(len(req)))
	respPtr := uint32((packed >> 32) & 0xFFFF_FFFF)
	respLen := uint32(packed & 0xFFFF_FFFF)
	if respPtr == 0 || respLen == 0 {
		return nil
	}
	return append([]byte{}, unsafe.Slice((*byte)(unsafe.Pointer(uintptr(respPtr))), respLen)...)
}

// emitEventImpl pushes an event to the host's WebView relay.
func emitEventImpl(name string, payload []byte) {
	namePtr := allocBytes([]byte(name))
	payloadPtr := allocBytes(payload)
	emitEventHost(namePtr, uint32(len(name)), payloadPtr, uint32(len(payload)))
}
