package main

import (
        "runtime"
        "wit_component/export_wit_world"
"wit_component/wit_async"
"wit_component/wit_runtime"
"wit_component/wit_types"
"unsafe"
)

var staticPinner = runtime.Pinner{}
var exportReturnArea = uintptr(wit_runtime.Allocate(&staticPinner, 0, 1))
var syncExportPinner = runtime.Pinner{}


//go:wasmexport [async-lift]run
func wasm_export_wit_world_run(arg0 uintptr, arg1 uint32) int32 {
        return int32(wit_async.Run(func() {
        pinner := &runtime.Pinner{}
        value := unsafe.Slice((*uint8)(unsafe.Pointer(arg0)), arg1)
wit_runtime.Unpin()
result := export_wit_world.Run(value)
var option int32
var option0 uintptr
var option1 uint32
switch result.Tag() {
case wit_types.ResultOk:
        payload := result.Ok()
        data := unsafe.Pointer(unsafe.SliceData(payload))
pinner.Pin(data)

        option = int32(0)
option0 = uintptr(data)
option1 = uint32(len(payload))
case wit_types.ResultErr:
        payload := result.Err()
        utf8 := unsafe.Pointer(unsafe.StringData(payload))
pinner.Pin(utf8)

        option = int32(1)
option0 = uintptr(utf8)
option1 = uint32(len(payload))
default:
        panic("unreachable")
}
wasm_export_task_return_wit_world_run(option, option0, option1)

        }))
}

//go:wasmexport [callback][async-lift]run
func wasm_export_callback_wit_world_run(event0 uint32, event1 uint32, event2 uint32) uint32 {
        return wit_async.Callback(event0, event1, event2)
}

//go:wasmimport [export]$root [task-return]run
func wasm_export_task_return_wit_world_run(arg0 int32, arg1 uintptr, arg2 uint32)



// Unused, but present to make the compiler happy
func main() {}
