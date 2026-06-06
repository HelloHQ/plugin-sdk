// Package hellohq is the Tier 2 (Wasm) HelloHQ plugin SDK for Go.
//
// Plugins compile to wasip1 and run in-process in the host via Wasmtime.
//
//	package main
//
//	import "github.com/HelloHQ/plugin-sdk/sdks/go"
//
//	func init() {
//	    hellohq.Register(func(in hellohq.Input) ([]byte, error) {
//	        names, err := hellohq.ReadPortfolioNames()
//	        if err != nil {
//	            return hellohq.UIEmptyState("Error", err.Error()), nil
//	        }
//	        rows := make([][2]string, len(names))
//	        for i, p := range names {
//	            rows[i] = [2]string{p.Name, p.ID}
//	        }
//	        return hellohq.UIColumn(
//	            hellohq.UIHeading("Portfolios"),
//	            hellohq.UIKeyValueList(rows...),
//	        ), nil
//	    })
//	}
//
// Build for the host:
//
//	GOOS=wasip1 GOARCH=wasm go build -o plugin.wasm
package hellohq

import (
	"encoding/json"
	"fmt"
)

// ProtocolVersion is the hellohq:plugin version this SDK targets.
const ProtocolVersion = "1.0.0"

// ─────────────────────────────────────────────────────────────────────────────
// Input / dispatch
// ─────────────────────────────────────────────────────────────────────────────

// Input is the JSON the host passes to run.
type Input struct {
	Function string         `json:"function"`
	Args     map[string]any `json:"args"`
}

// RunFunc handles a single invocation. Return JSON bytes conforming to the
// declarative UI schema, or an error.
type RunFunc func(in Input) ([]byte, error)

var registered RunFunc

// Register sets the plugin's run handler. Call it from your package init.
func Register(fn RunFunc) { registered = fn }

func invoke(raw []byte) []byte {
	if registered == nil {
		return encodeError("no run handler registered")
	}
	var in Input
	if err := json.Unmarshal(raw, &in); err != nil {
		return encodeError("invalid input json")
	}
	out, err := registered(in)
	if err != nil {
		return encodeError(err.Error())
	}
	return out
}

func encodeError(msg string) []byte {
	b, _ := json.Marshal(map[string]string{"error": msg})
	return b
}

// ─────────────────────────────────────────────────────────────────────────────
// Host API types
// ─────────────────────────────────────────────────────────────────────────────

// PortfolioName is a portfolio id + display name returned by ReadPortfolioNames.
type PortfolioName struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// CurrencyRate is a workspace currency and its rate relative to the base.
type CurrencyRate struct {
	ID     string  `json:"id"`
	Name   string  `json:"name"`
	Symbol string  `json:"symbol"`
	Rate   float64 `json:"rate"`
}

// HostError is returned when a host read fails.
type HostError struct {
	Kind    string
	Message string
}

// ── AI inference types (v2 feature — requires ai:inference permission) ────────

// AiMessage is a single turn in an AI conversation.
type AiMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// UserMessage constructs a user-role AiMessage.
func UserMessage(content string) AiMessage { return AiMessage{Role: "user", Content: content} }

// AssistantMessage constructs an assistant-role AiMessage.
func AssistantMessage(content string) AiMessage {
	return AiMessage{Role: "assistant", Content: content}
}

// InferenceOpts controls the AI completion call.
type InferenceOpts struct {
	MaxTokens   uint32   `json:"max_tokens"`
	Temperature *float32 `json:"temperature,omitempty"`
}

// InferenceResponse is returned by AiComplete.
type InferenceResponse struct {
	Content      string `json:"content"`
	InputTokens  uint32 `json:"input_tokens"`
	OutputTokens uint32 `json:"output_tokens"`
	Model        string `json:"model"`
}

func (e *HostError) Error() string {
	return fmt.Sprintf("host error (%s): %s", e.Kind, e.Message)
}

// ─────────────────────────────────────────────────────────────────────────────
// Host reads — typed wrappers over the hq_read JSON protocol
// ─────────────────────────────────────────────────────────────────────────────

// ReadPortfolioNames returns the id and display name of every portfolio in the
// active workspace. Requires the read:portfolio_names permission.
func ReadPortfolioNames() ([]PortfolioName, error) {
	return readTyped[[]PortfolioName]("read:portfolio_names", "")
}

// ReadSheetStructure returns the sheet/section/item names for a portfolio.
// Requires the read:sheet_structure permission. Pass an empty string for all
// portfolios.
func ReadSheetStructure(portfolioID string) (any, error) {
	return readRaw("read:sheet_structure", portfolioID)
}

// ReadAssetCount returns the item counts by category for a portfolio.
// Requires the read:asset_count permission.
func ReadAssetCount(portfolioID string) (any, error) {
	return readRaw("read:asset_count", portfolioID)
}

// ReadCurrencyRates returns the workspace currencies and their exchange rates.
// Requires the read:currency_rates permission.
func ReadCurrencyRates() ([]CurrencyRate, error) {
	return readTyped[[]CurrencyRate]("read:currency_rates", "")
}

// ReadAggregatedValues returns pre-aggregated portfolio totals (Verified tier).
// Requires the read:aggregated_values permission.
func ReadAggregatedValues(portfolioID string) (any, error) {
	return readRaw("read:aggregated_values", portfolioID)
}

// EmitEvent pushes a fire-and-forget event to the plugin's WebView.
// No-op in declarative/headless mode.
func EmitEvent(name string, payload []byte) {
	emitEventImpl(name, payload)
}

// AiComplete routes a message list through the host AI backend.
// The plugin never holds an API key; the host proxies via HQAuthProxy.
// Requires the ai:inference manifest permission (v2 feature).
func AiComplete(messages []AiMessage, opts InferenceOpts) (*InferenceResponse, error) {
	type aiReq struct {
		Method   string        `json:"method"`
		Messages []AiMessage   `json:"messages"`
		Opts     InferenceOpts `json:"opts"`
	}
	reqBytes, _ := json.Marshal(aiReq{Method: "ai:complete", Messages: messages, Opts: opts})
	respBytes := hqReadImpl(reqBytes)
	if respBytes == nil {
		return nil, &HostError{Kind: "transport", Message: "nil response from host"}
	}
	var resp hqResponse
	if err := json.Unmarshal(respBytes, &resp); err != nil {
		return nil, &HostError{Kind: "decode", Message: err.Error()}
	}
	if !resp.OK {
		return nil, parseHostError(resp.Error)
	}
	var out InferenceResponse
	if err := json.Unmarshal(resp.Data, &out); err != nil {
		return nil, &HostError{Kind: "decode", Message: err.Error()}
	}
	return &out, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

type hqRequest struct {
	Method      string `json:"method"`
	PortfolioID string `json:"portfolio_id,omitempty"`
}

type hqResponse struct {
	OK    bool            `json:"ok"`
	Data  json.RawMessage `json:"data,omitempty"`
	Error string          `json:"error,omitempty"`
}

func readRaw(method, portfolioID string) (any, error) {
	reqBytes, _ := json.Marshal(hqRequest{Method: method, PortfolioID: portfolioID})
	respBytes := hqReadImpl(reqBytes)
	if respBytes == nil {
		return nil, &HostError{Kind: "transport", Message: "nil response from host"}
	}
	var resp hqResponse
	if err := json.Unmarshal(respBytes, &resp); err != nil {
		return nil, &HostError{Kind: "decode", Message: err.Error()}
	}
	if !resp.OK {
		return nil, parseHostError(resp.Error)
	}
	var v any
	if err := json.Unmarshal(resp.Data, &v); err != nil {
		return nil, &HostError{Kind: "decode", Message: err.Error()}
	}
	return v, nil
}

func readTyped[T any](method, portfolioID string) (T, error) {
	reqBytes, _ := json.Marshal(hqRequest{Method: method, PortfolioID: portfolioID})
	respBytes := hqReadImpl(reqBytes)
	var zero T
	if respBytes == nil {
		return zero, &HostError{Kind: "transport", Message: "nil response from host"}
	}
	var resp hqResponse
	if err := json.Unmarshal(respBytes, &resp); err != nil {
		return zero, &HostError{Kind: "decode", Message: err.Error()}
	}
	if !resp.OK {
		return zero, parseHostError(resp.Error)
	}
	var out T
	if err := json.Unmarshal(resp.Data, &out); err != nil {
		return zero, &HostError{Kind: "decode", Message: err.Error()}
	}
	return out, nil
}

func parseHostError(raw string) *HostError {
	if raw == "" {
		raw = "unknown error"
	}
	if len(raw) > 7 && raw[:7] == "denied:" {
		return &HostError{Kind: "permission_denied", Message: raw}
	}
	return &HostError{Kind: "error", Message: raw}
}
