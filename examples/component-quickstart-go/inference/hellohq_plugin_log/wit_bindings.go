package hellohq_plugin_log

import (
        "unsafe"
"runtime"
)


const (
        LevelTrace uint8 = 0
LevelDebug uint8 = 1
LevelInfo uint8 = 2
LevelWarn uint8 = 3
LevelError uint8 = 4
)
type Level = uint8

//go:wasmimport hellohq:plugin/log@0.1.0 write
func wasm_import_write(arg0 int32, arg1 uintptr, arg2 uint32) 

func Write(level Level, message string)  {
        pinner := &runtime.Pinner{}
defer pinner.Unpin()

        
        utf8 := unsafe.Pointer(unsafe.StringData(message))
pinner.Pin(utf8)
wasm_import_write(int32(level), uintptr(utf8), uint32(len(message)))

}
