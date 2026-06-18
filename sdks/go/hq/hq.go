// SPDX-License-Identifier: Apache-2.0

// Package hq is the ergonomic, stable author API over the raw wit-bindgen-go
// bindings for HelloHQ Tier-2 (Component Model) plugins.
//
// Every function maps 1:1 onto a `hellohq:plugin/*@0.1.0` import. Reads return
// `(value, *Error)`; the host permission-gates each call and surfaces a denial
// as an [Error] with a stable Code (e.g. "permission-denied").
//
// The host only sees an import the plugin actually calls: `wasm-tools` /
// TinyGo emit only the interfaces referenced by the linked component, so a
// plugin that touches only `storage` imports only `storage` (+ `types`).
//
// This package is built against the supplementary `hellohq-plugin-component`
// world (canonical interfaces MINUS `inference`); see wit/component.wit and
// the package README for why `inference` (WASI-0.3 `stream<string>`) is
// currently omitted from the Go path.
package hq

import (
	"go.bytecodealliance.org/cm"

	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/events"
	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/log"
	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/storage"
	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/types"
	"github.com/HelloHQ/plugin-sdk/sdks/go/internal/bindings/hellohq/plugin/workspace"
)

// ─────────────────────────────────────────────────────────────────────────────
// Re-exported generated types (stable author-facing names)
// ─────────────────────────────────────────────────────────────────────────────

type (
	// PortfolioName is a portfolio id + display name.
	PortfolioName = types.PortfolioName
	// CurrencyRate is a workspace currency and its rate vs the base currency.
	CurrencyRate = types.CurrencyRate
	// SheetInfo is a sheet name + its section names.
	SheetInfo = types.SheetInfo
	// SheetSummary is the sheet/section structure of a portfolio.
	SheetSummary = types.SheetSummary
	// CategoryCount is an item count for one category.
	CategoryCount = types.CategoryCount
	// AssetCount is per-category item counts for a portfolio.
	AssetCount = types.AssetCount
	// CategoryTotal is an aggregated total for one category.
	CategoryTotal = types.CategoryTotal
	// AggregatedSummary is per-category portfolio totals (Verified tier).
	AggregatedSummary = types.AggregatedSummary
	// PluginEvent is a kind + opaque payload pushed to the host.
	PluginEvent = events.PluginEvent
	// LogLevel selects the severity of a log line.
	LogLevel = log.Level
)

// Log levels (re-exported).
const (
	LevelTrace = log.LevelTrace
	LevelDebug = log.LevelDebug
	LevelInfo  = log.LevelInfo
	LevelWarn  = log.LevelWarn
	LevelError = log.LevelError
)

// Error is a host gate denial, validation failure, or downstream error. Code is
// a stable machine token ("permission-denied" | "origin-blocked" |
// "address-blocked" | "rate-limited" | "not-found"); Message is safe to show
// the user. It carries no secret, raw prompt/response, credential id, or request
// id (the AI-harness boundary rules).
type Error struct {
	Code    string
	Message string
}

func (e *Error) Error() string {
	if e.Code == "" {
		return e.Message
	}
	return e.Code + ": " + e.Message
}

// fromAPIError converts a generated APIError into the ergonomic *Error.
func fromAPIError(e types.APIError) *Error {
	return &Error{Code: e.Code, Message: e.Message}
}

// ─────────────────────────────────────────────────────────────────────────────
// Workspace — read-only, permission-gated per function.
// ─────────────────────────────────────────────────────────────────────────────

// Workspace groups the read-only workspace capabilities. Each method requires
// the matching manifest permission; ReadAggregatedValues additionally requires
// Verified trust + per-portfolio scope.
var Workspace workspaceAPI

type workspaceAPI struct{}

// ReadPortfolioNames returns every portfolio's id + display name.
// (read:portfolio_names)
func (workspaceAPI) ReadPortfolioNames() ([]PortfolioName, *Error) {
	ok, err, isErr := workspace.ReadPortfolioNames().Result()
	if isErr {
		return nil, fromAPIError(err)
	}
	return ok.Slice(), nil
}

// ReadSheetStructure returns the sheet/section names for a portfolio (never
// values). (read:sheet_structure)
func (workspaceAPI) ReadSheetStructure(portfolioID string) (SheetSummary, *Error) {
	ok, err, isErr := workspace.ReadSheetStructure(portfolioID).Result()
	if isErr {
		return SheetSummary{}, fromAPIError(err)
	}
	return ok, nil
}

// ReadAssetCount returns item counts split by category for a portfolio.
// (read:asset_count)
func (workspaceAPI) ReadAssetCount(portfolioID string) (AssetCount, *Error) {
	ok, err, isErr := workspace.ReadAssetCount(portfolioID).Result()
	if isErr {
		return AssetCount{}, fromAPIError(err)
	}
	return ok, nil
}

// ReadCurrencyRates returns the workspace currencies and exchange rates.
// (read:currency_rates)
func (workspaceAPI) ReadCurrencyRates() ([]CurrencyRate, *Error) {
	ok, err, isErr := workspace.ReadCurrencyRates().Result()
	if isErr {
		return nil, fromAPIError(err)
	}
	return ok.Slice(), nil
}

// ReadAggregatedValues returns per-category portfolio totals.
// (read:aggregated_values, Verified tier)
func (workspaceAPI) ReadAggregatedValues(portfolioID string) (AggregatedSummary, *Error) {
	ok, err, isErr := workspace.ReadAggregatedValues(portfolioID).Result()
	if isErr {
		return AggregatedSummary{}, fromAPIError(err)
	}
	return ok, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Storage — per-plugin key-value, host-keyed by (plugin_id, key).
// ─────────────────────────────────────────────────────────────────────────────

// Storage groups the per-plugin key-value capabilities. The host enforces a
// per-plugin quota.
var Storage storageAPI

type storageAPI struct{}

// Get reads a value. Returns (nil, nil) if the key is absent.
func (storageAPI) Get(key string) ([]byte, *Error) {
	ok, err, isErr := storage.Get(key).Result()
	if isErr {
		return nil, fromAPIError(err)
	}
	if ok.None() {
		return nil, nil
	}
	return ok.Value().Slice(), nil
}

// Set writes a value (overwrites any existing).
func (storageAPI) Set(key string, value []byte) *Error {
	_, err, isErr := storage.Set(key, cm.ToList(value)).Result()
	if isErr {
		return fromAPIError(err)
	}
	return nil
}

// Delete removes a key (no-op if absent).
func (storageAPI) Delete(key string) *Error {
	_, err, isErr := storage.Delete(key).Result()
	if isErr {
		return fromAPIError(err)
	}
	return nil
}

// Clear deletes all of this plugin's keys.
func (storageAPI) Clear() *Error {
	_, err, isErr := storage.Clear().Result()
	if isErr {
		return fromAPIError(err)
	}
	return nil
}

// ListKeys lists all keys this plugin has stored (keys only, no values).
func (storageAPI) ListKeys() ([]string, *Error) {
	ok, err, isErr := storage.ListKeys().Result()
	if isErr {
		return nil, fromAPIError(err)
	}
	return ok.Slice(), nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Events — push from plugin to host (size + rate capped host-side).
// ─────────────────────────────────────────────────────────────────────────────

// Events groups the push-event capability.
var Events eventsAPI

type eventsAPI struct{}

// Emit pushes an event. kind is a stable tag; payload is opaque bytes.
func (eventsAPI) Emit(kind string, payload []byte) *Error {
	_, err, isErr := events.Emit(events.PluginEvent{
		Kind:    kind,
		Payload: cm.ToList(payload),
	}).Result()
	if isErr {
		return fromAPIError(err)
	}
	return nil
}

// EmitEvent pushes a pre-built PluginEvent.
func (eventsAPI) EmitEvent(event PluginEvent) *Error {
	_, err, isErr := events.Emit(event).Result()
	if isErr {
		return fromAPIError(err)
	}
	return nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Log — always available, never permission-gated.
// ─────────────────────────────────────────────────────────────────────────────

// Log groups structured logging. Always available, never gated.
var Log logAPI

type logAPI struct{}

// Write logs at an explicit level.
func (logAPI) Write(level LogLevel, message string) { log.Write(level, message) }

// Trace logs at trace level.
func (logAPI) Trace(message string) { log.Write(log.LevelTrace, message) }

// Debug logs at debug level.
func (logAPI) Debug(message string) { log.Write(log.LevelDebug, message) }

// Info logs at info level.
func (logAPI) Info(message string) { log.Write(log.LevelInfo, message) }

// Warn logs at warn level.
func (logAPI) Warn(message string) { log.Write(log.LevelWarn, message) }

// Error logs at error level.
func (logAPI) Error(message string) { log.Write(log.LevelError, message) }
