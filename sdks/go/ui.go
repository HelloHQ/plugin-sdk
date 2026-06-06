package hellohq

import "encoding/json"

// Declarative UI builders — produce the JSON the host renders natively.
// Mirror the 15-component schema used by the Rust SDK and documented in
// docs/plugin/04_ui-declarative.md.
//
// All functions return JSON-encoded bytes so plugins can return them directly
// from their RunFunc without an extra Marshal call.

func UIColumn(children ...[]byte) []byte  { return container("column", children) }
func UIRow(children ...[]byte) []byte     { return container("row", children) }

func UISection(title string, children ...[]byte) []byte {
	raw := childNodes(children)
	return marshal(map[string]any{"type": "section", "title": title, "children": raw})
}

func UIHeading(text string) []byte {
	return marshal(map[string]any{"type": "heading", "text": text})
}

func UIText(text string) []byte {
	return marshal(map[string]any{"type": "text", "text": text})
}

func UIDivider() []byte  { return marshal(map[string]any{"type": "divider"}) }
func UILoading() []byte  { return marshal(map[string]any{"type": "loading"}) }

// UIKeyValueList renders a label→value list. Each entry is [2]string{label, value}.
func UIKeyValueList(items ...[2]string) []byte {
	rows := make([]map[string]string, len(items))
	for i, it := range items {
		rows[i] = map[string]string{"label": it[0], "value": it[1]}
	}
	return marshal(map[string]any{"type": "key-value-list", "items": rows})
}

// UIMetric renders a single KPI tile. delta is optional (pass "" to omit).
func UIMetric(label, value, delta string) []byte {
	m := map[string]any{"type": "metric", "label": label, "value": value}
	if delta != "" {
		m["delta"] = delta
	}
	return marshal(m)
}

// UITable renders a table. columns are header labels; rows are cell-string slices.
func UITable(columns []string, rows [][]string) []byte {
	cols := make([]map[string]string, len(columns))
	for i, c := range columns {
		cols[i] = map[string]string{"label": c}
	}
	return marshal(map[string]any{"type": "table", "columns": cols, "rows": rows})
}

// UIButton renders an action button. function and args are dispatched to run.
func UIButton(label, function string, args map[string]any) []byte {
	return marshal(map[string]any{
		"type":   "button",
		"label":  label,
		"on-tap": map[string]any{"function": function, "args": args},
	})
}

// UISelect renders a dropdown. Each option is [2]string{value, label}.
func UISelect(options [][2]string, function string) []byte {
	opts := make([]map[string]string, len(options))
	for i, o := range options {
		opts[i] = map[string]string{"value": o[0], "label": o[1]}
	}
	return marshal(map[string]any{
		"type":    "select",
		"options": opts,
		"action":  map[string]any{"function": function, "args": map[string]any{}},
	})
}

// UIBadgeRow renders a row of coloured badges. Each badge is [2]string{label, color}.
func UIBadgeRow(badges ...[2]string) []byte {
	b := make([]map[string]string, len(badges))
	for i, bg := range badges {
		b[i] = map[string]string{"label": bg[0], "color": bg[1]}
	}
	return marshal(map[string]any{"type": "badge-row", "badges": b})
}

// UIEmptyState renders the empty/error placeholder. description is optional.
func UIEmptyState(title, description string) []byte {
	m := map[string]any{"type": "empty-state", "title": title}
	if description != "" {
		m["description"] = description
	}
	return marshal(m)
}

// UIChart renders a chart. kind is e.g. "line"/"bar"; series is plugin-defined JSON.
func UIChart(kind string, series any) []byte {
	return marshal(map[string]any{"type": "chart", "chart_type": kind, "series": series})
}

// ─────────────────────────────────────────────────────────────────────────────
// internal helpers
// ─────────────────────────────────────────────────────────────────────────────

func marshal(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

func childNodes(children [][]byte) []json.RawMessage {
	nodes := make([]json.RawMessage, 0, len(children))
	for _, c := range children {
		if len(c) > 0 {
			nodes = append(nodes, json.RawMessage(c))
		}
	}
	return nodes
}

func container(kind string, children [][]byte) []byte {
	return marshal(map[string]any{
		"type":     kind,
		"children": childNodes(children),
	})
}
