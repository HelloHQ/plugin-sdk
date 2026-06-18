// SPDX-License-Identifier: Apache-2.0

// Package component wires a Go plugin into the canonical
// `hellohq:plugin/guest@0.1.0` Component Model exports (init / run / metadata).
//
// This is the Tier-2 (Component Model + WASI 0.3) entry point. Implement the
// [Plugin] interface and call [Export] from an init function; build with
// TinyGo's wasip2 target (see the SDK README and examples/component-quickstart-go).
//
//	package main
//
//	import (
//	    "github.com/HelloHQ/plugin-sdk/sdks/go/component"
//	    "github.com/HelloHQ/plugin-sdk/sdks/go/hq"
//	)
//
//	type myPlugin struct{}
//
//	func (myPlugin) Init() { hq.Log.Info("my-plugin: init") }
//
//	func (myPlugin) Run(input []byte) ([]byte, error) {
//	    names, err := hq.Workspace.ReadPortfolioNames()
//	    if err != nil {
//	        return nil, err
//	    }
//	    return []byte(fmt.Sprintf("%d portfolios", len(names))), nil
//	}
//
//	func (myPlugin) Metadata() component.Metadata {
//	    return component.Metadata{ID: "my-plugin", Version: "0.1.0"}
//	}
//
//	func init() { component.Export(myPlugin{}) }
//
//	func main() {} // required by TinyGo; never called for a reactor component
package component

import (
	"go.bytecodealliance.org/cm"

	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/guest"
)

// ProtocolVersion is the hellohq:plugin WIT package version this SDK targets.
// Kept in sync across the Rust/JS/Python/Go SDKs by CI's version-consistency job.
const ProtocolVersion = "0.1.0"

// Metadata is the static identity a plugin reports to the host (the canonical
// `hellohq:plugin/guest#plugin-metadata` record).
type Metadata = guest.PluginMetadata

// Plugin is the set of exports every HelloHQ Tier-2 plugin provides (the
// canonical `hellohq:plugin/guest@0.1.0` interface). Implement it on a struct
// and wire it with [Export].
type Plugin interface {
	// Init is called once after the component is instantiated, before any Run.
	// Use it to log a banner or warm caches.
	Init()

	// Run is the plugin's main entry point. input is opaque bytes from the
	// host; the returned bytes are handed back. On error, the host surfaces the
	// message (and degrades the pane gracefully).
	Run(input []byte) ([]byte, error)

	// Metadata returns the plugin's static identity.
	Metadata() Metadata
}

// Export wires a [Plugin] implementation into the canonical
// `hellohq:plugin/guest` component exports. Call it once from an init function.
func Export(p Plugin) {
	guest.Exports.Init = func() {
		p.Init()
	}

	guest.Exports.Run = func(input cm.List[uint8]) cm.Result[cm.List[uint8], cm.List[uint8], string] {
		out, err := p.Run(input.Slice())
		if err != nil {
			return cm.Err[cm.Result[cm.List[uint8], cm.List[uint8], string]](err.Error())
		}
		return cm.OK[cm.Result[cm.List[uint8], cm.List[uint8], string]](cm.ToList(out))
	}

	guest.Exports.Metadata = func() Metadata {
		return p.Metadata()
	}
}
