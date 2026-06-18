// SPDX-License-Identifier: Apache-2.0

// HelloHQ Tier-2 Component Model quickstart (Go / TinyGo).
//
// A small headless plugin built with the HelloHQ Go SDK against the canonical
// `hellohq:plugin@0.1.0` WIT (via the supplementary `hellohq-plugin-component`
// build world — see ../../sdks/go/wit/component.wit). On run it:
//
//  1. logs a banner (hq.Log),
//  2. reads the workspace portfolio names (hq.Workspace),
//  3. stores + reads back a value (hq.Storage),
//  4. emits an event (hq.Events),
//  5. returns a compact ASCII summary "<n-portfolios>|<roundtrip-ok>".
//
// It deliberately touches only workspace / storage / events / log, so the built
// component imports only those `hellohq:plugin/*@0.1.0` interfaces (+ types).
// Mirrors examples/component-quickstart (Rust) and component-quickstart-js.
package main

import (
	"fmt"

	"github.com/HelloHQ/plugin-sdk/sdks/go/component"
	"github.com/HelloHQ/plugin-sdk/sdks/go/hq"
)

type quickstart struct{}

func (quickstart) Init() {
	hq.Log.Info("component-quickstart-go: init")
}

func (quickstart) Run(_ []byte) ([]byte, error) {
	hq.Log.Debug("component-quickstart-go: run start")

	// 1. Read workspace portfolio names (permission-gated).
	names, err := hq.Workspace.ReadPortfolioNames()
	if err != nil {
		return nil, err
	}
	hq.Log.Info(fmt.Sprintf("read %d portfolio name(s)", len(names)))

	// 2. Storage round-trip: set "greeting" -> read it back.
	if err := hq.Storage.Set("greeting", []byte("hello")); err != nil {
		return nil, err
	}
	got, err := hq.Storage.Get("greeting")
	if err != nil {
		return nil, err
	}
	roundtrip := string(got) == "hello"

	// 3. Emit an event (best-effort; ignore a cap/denial here).
	_ = hq.Events.Emit("quickstart-ran", []byte("ok"))

	// 4. Compact summary the host can assert: "<n>|<ok>", e.g. "3|1".
	ok := 0
	if roundtrip {
		ok = 1
	}
	hq.Log.Debug("component-quickstart-go: run done")
	return []byte(fmt.Sprintf("%d|%d", len(names), ok)), nil
}

func (quickstart) Metadata() component.Metadata {
	return component.Metadata{ID: "component-quickstart-go", Version: "0.1.0"}
}

func init() {
	component.Export(quickstart{})
}

// main is required by TinyGo. For a reactor-style component it is never called;
// the host invokes the `hellohq:plugin/guest` exports directly.
func main() {}
