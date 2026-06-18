package hellohq_plugin_inference

import (
        "wit_component/hellohq_plugin_types"
"wit_component/wit_runtime"
"wit_component/wit_types"
"unsafe"
"runtime"
)


//go:wasmimport hellohq:plugin/inference@0.1.0 [stream-new-0]complete
func wasm_stream_new_string() uint64

//go:wasmimport hellohq:plugin/inference@0.1.0 [async-lower][stream-read-0]complete
func wasm_stream_read_string(handle int32, item unsafe.Pointer, count uint32) uint32

//go:wasmimport hellohq:plugin/inference@0.1.0 [async-lower][stream-write-0]complete
func wasm_stream_write_string(handle int32, item unsafe.Pointer, count uint32) uint32

//go:wasmimport hellohq:plugin/inference@0.1.0 [stream-drop-readable-0]complete
func wasm_stream_drop_readable_string(handle int32)

//go:wasmimport hellohq:plugin/inference@0.1.0 [stream-drop-writable-0]complete
func wasm_stream_drop_writable_string(handle int32)

func wasm_stream_lift_string(src unsafe.Pointer) string {
        value := unsafe.String((*uint8)(unsafe.Pointer(uintptr(*(*uint32)(unsafe.Add(unsafe.Pointer(src), 0))))), *(*uint32)(unsafe.Add(unsafe.Pointer(src), 4)))

	return value
}


func wasm_stream_lower_string(pinner *runtime.Pinner, value string, dst unsafe.Pointer) {
        utf8 := unsafe.Pointer(unsafe.StringData(value))
pinner.Pin(utf8)
*(*uint32)(unsafe.Add(unsafe.Pointer(dst), 4)) = uint32(uint32(len(value)))
*(*uint32)(unsafe.Add(unsafe.Pointer(dst), 0)) = uint32(uintptr(uintptr(utf8)))

}


var wasm_stream_vtable_string = wit_types.StreamVtable[string]{
	(2*4),
	4,
	wasm_stream_read_string,
	wasm_stream_write_string,
	nil,
	nil,
	wasm_stream_drop_readable_string,
	wasm_stream_drop_writable_string,
	wasm_stream_lift_string,
	wasm_stream_lower_string,
}

func MakeStreamString() (*wit_types.StreamWriter[string], *wit_types.StreamReader[string]) {
	pair := wasm_stream_new_string()
	return wit_types.MakeStreamWriter[string](&wasm_stream_vtable_string, int32(pair >> 32)),
		wit_types.MakeStreamReader[string](&wasm_stream_vtable_string, int32(pair & 0xFFFFFFFF))
}

func LiftStreamString(handle int32) *wit_types.StreamReader[string] {
	return wit_types.MakeStreamReader[string](&wasm_stream_vtable_string, handle)
}
type ApiError = hellohq_plugin_types.ApiError

type ChatMessage struct {
        Role string
Content string 
}

// role: system|user|assistant
type InferenceOpts struct {
        MaxTokens uint32
Temperature wit_types.Option[float32] 
}

//go:wasmimport hellohq:plugin/inference@0.1.0 complete
func wasm_import_complete(arg0 uintptr, arg1 uint32, arg2 int32, arg3 int32, arg4 float32, arg5 uintptr) 

func Complete(messages []ChatMessage, opts InferenceOpts) wit_types.Result[*wit_types.StreamReader[string], hellohq_plugin_types.ApiError] {
        pinner := &runtime.Pinner{}
defer pinner.Unpin()

        returnArea := uintptr(wit_runtime.Allocate(pinner, (5*4), 4))
        slice := messages
length := uint32(len(slice))
result := wit_runtime.Allocate(pinner, uintptr(length * (4*4)), 4)
for index, element := range slice {
        base := unsafe.Add(result, index * (4*4))
        utf8 := unsafe.Pointer(unsafe.StringData((element).Role))
pinner.Pin(utf8)
*(*uint32)(unsafe.Add(unsafe.Pointer(base), 4)) = uint32(uint32(len((element).Role)))
*(*uint32)(unsafe.Add(unsafe.Pointer(base), 0)) = uint32(uintptr(uintptr(utf8)))
utf80 := unsafe.Pointer(unsafe.StringData((element).Content))
pinner.Pin(utf80)
*(*uint32)(unsafe.Add(unsafe.Pointer(base), (3*4))) = uint32(uint32(len((element).Content)))
*(*uint32)(unsafe.Add(unsafe.Pointer(base), (2*4))) = uint32(uintptr(uintptr(utf80)))

}

var option int32
var option1 float32
switch (opts).Temperature.Tag() {
case wit_types.OptionNone:
        
        option = int32(0)
option1 = 0
case wit_types.OptionSome:
        payload := (opts).Temperature.Some()
        
        option = int32(1)
option1 = payload
default:
        panic("unreachable")
}
wasm_import_complete(uintptr(result), length, int32((opts).MaxTokens), option, option1, returnArea)
var result3 wit_types.Result[*wit_types.StreamReader[string], hellohq_plugin_types.ApiError]
switch uint8(*(*uint32)(unsafe.Add(unsafe.Pointer(returnArea), 0))) {
case 0:
        
        result3 = wit_types.Ok[*wit_types.StreamReader[string], hellohq_plugin_types.ApiError](LiftStreamString(*(*int32)(unsafe.Add(unsafe.Pointer(returnArea), 4))))
case 1:
        value := unsafe.String((*uint8)(unsafe.Pointer(uintptr(*(*uint32)(unsafe.Add(unsafe.Pointer(returnArea), 4))))), *(*uint32)(unsafe.Add(unsafe.Pointer(returnArea), (2*4))))
value2 := unsafe.String((*uint8)(unsafe.Pointer(uintptr(*(*uint32)(unsafe.Add(unsafe.Pointer(returnArea), (3*4)))))), *(*uint32)(unsafe.Add(unsafe.Pointer(returnArea), (4*4))))

        result3 = wit_types.Err[*wit_types.StreamReader[string], hellohq_plugin_types.ApiError](hellohq_plugin_types.ApiError{value, value2})
default:
        panic("unreachable")
}
result4 := result3;
return result4

}
