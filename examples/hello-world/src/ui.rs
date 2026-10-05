// SPDX-License-Identifier: Apache-2.0
//
//! The declarative UI documents Hello World returns from `run`.
//!
//! Pure functions (no host calls) so they are unit-tested on the host with
//! `cargo test`.
//!
//! ## Output contract
//!
//! A `ui_type: "declarative"` plugin's `run` returns UTF-8 JSON whose top level
//! is ONE component object (`{"type": …}`); the app decodes it and hands it to
//! its native renderer. Matched against the app (HelloHQ/hellohq):
//!
//!   - lib/app/presentation/screens/pages/plugin_page_view/plugin_page_view_body/plugin_runner_view.dart
//!     (`jsonDecode(output)` must be a `Map<String, dynamic>`, else "invalid
//!     response"; the first run's input is `{"function":"main","args":{}}`),
//!   - lib/app/presentation/widgets/plugin/declarative/plugin_declarative_renderer.dart
//!     (`PluginDeclarativeView._node` — the node types it renders),
//!   - lib/app/presentation/widgets/plugin/declarative/plugin_declarative_models.dart
//!     (`PluginUiLimits` — e.g. `maxTableRows = 500`, `maxChildren = 50`),
//!
//! and the design doc hellohqworkspace/docs/plugin/06-ui-declarative.md.
//!
//! Only these node types (all rendered by `_node`) are emitted, with the fields
//! the renderer reads:
//!
//!   - `column`      `children`
//!   - `heading`     `text`, `level`
//!   - `text`        `content`, `color`, `size`
//!   - `table`       `columns[{key,label}]`, `rows[{<key>: string}]`
//!   - `empty-state` `title`, `description`
//!
//! The JSON is written by hand (no serde) to keep the `no_std` component small;
//! every plugin-supplied string goes through [`push_json_string`].

use alloc::string::String;

/// The renderer truncates a table to this many rows
/// (`PluginUiLimits.maxTableRows`); we cap explicitly and say so instead.
pub const MAX_TABLE_ROWS: usize = 500;

const HEADING: &str = "Hello, World!";

/// The document for a successful read: the names in a one-column table, or a
/// friendly empty state when the workspace has no portfolios.
pub fn portfolio_list<S: AsRef<str>>(names: &[S]) -> String {
    let mut out = String::with_capacity(128 + names.len() * 32);
    out.push_str("{\"type\":\"column\",\"children\":[");
    push_heading(&mut out);
    out.push(',');

    if names.is_empty() {
        push_empty_state(
            &mut out,
            "No portfolios yet",
            "Create a portfolio in HelloHQ and its name will be listed here.",
        );
    } else {
        let n = names.len();
        out.push_str("{\"type\":\"text\",\"color\":\"muted\",\"content\":");
        let mut summary = String::from("You have ");
        push_usize(&mut summary, n);
        summary.push_str(if n == 1 { " portfolio" } else { " portfolios" });
        summary.push_str(" in this workspace.");
        push_json_string(&mut out, &summary);
        out.push_str("},");

        out.push_str(
            "{\"type\":\"table\",\"columns\":[{\"key\":\"name\",\"label\":\"Portfolio\"}],\"rows\":[",
        );
        for (i, name) in names.iter().take(MAX_TABLE_ROWS).enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push_str("{\"name\":");
            push_json_string(&mut out, name.as_ref());
            out.push('}');
        }
        out.push_str("]}");

        if n > MAX_TABLE_ROWS {
            out.push_str(",{\"type\":\"text\",\"color\":\"muted\",\"size\":\"sm\",\"content\":");
            let mut note = String::from("Showing the first ");
            push_usize(&mut note, MAX_TABLE_ROWS);
            note.push_str(" of ");
            push_usize(&mut note, n);
            note.push_str(" portfolios.");
            push_json_string(&mut out, &note);
            out.push('}');
        }
    }

    out.push_str("]}");
    out
}

/// The document when the host refuses or fails the read (for example the
/// `read:portfolio_names` grant was withdrawn). Never echoes the host's error
/// text into the UI; the plugin logs it instead.
pub fn names_unavailable() -> String {
    let mut out = String::from("{\"type\":\"column\",\"children\":[");
    push_heading(&mut out);
    out.push(',');
    push_empty_state(
        &mut out,
        "Portfolio names unavailable",
        "Hello World could not read your portfolio names. Check that the plugin \
         still has the \"Read portfolio names\" permission.",
    );
    out.push_str("]}");
    out
}

fn push_heading(out: &mut String) {
    out.push_str("{\"type\":\"heading\",\"level\":1,\"text\":");
    push_json_string(out, HEADING);
    out.push('}');
}

fn push_empty_state(out: &mut String, title: &str, description: &str) {
    out.push_str("{\"type\":\"empty-state\",\"title\":");
    push_json_string(out, title);
    out.push_str(",\"description\":");
    push_json_string(out, description);
    out.push('}');
}

/// Append `n` in decimal (no `format!`, keeping core::fmt out of the build).
fn push_usize(out: &mut String, mut n: usize) {
    let mut buf = [0u8; 20];
    let mut i = buf.len();
    loop {
        i -= 1;
        buf[i] = b'0' + (n % 10) as u8;
        n /= 10;
        if n == 0 {
            break;
        }
    }
    for &b in &buf[i..] {
        out.push(b as char);
    }
}

/// Append `s` as a JSON string literal (RFC 8259 §7): quote, backslash and
/// every control character below U+0020 are escaped; everything else is
/// copied through as UTF-8.
pub fn push_json_string(out: &mut String, s: &str) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let v = c as u32 as usize;
                out.push_str("\\u00");
                out.push(HEX[v >> 4] as char);
                out.push(HEX[v & 0xf] as char);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::string::ToString;
    use alloc::vec::Vec;
    use serde_json::{json, Value};

    fn parse(doc: &str) -> Value {
        serde_json::from_str(doc).expect("valid JSON")
    }

    fn children(doc: &Value) -> &Vec<Value> {
        assert_eq!(doc["type"], "column", "root is a column");
        doc["children"].as_array().expect("children array")
    }

    fn table_names(doc: &Value) -> Vec<String> {
        let table = children(doc)
            .iter()
            .find(|c| c["type"] == "table")
            .expect("a table");
        assert_eq!(
            table["columns"],
            json!([{"key": "name", "label": "Portfolio"}])
        );
        table["rows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["name"].as_str().unwrap().to_string())
            .collect()
    }

    /// Every node is one the app's renderer handles (plugin_declarative_renderer.dart).
    fn assert_known_types(node: &Value) {
        const RENDERED: &[&str] = &[
            "column",
            "row",
            "section",
            "heading",
            "text",
            "divider",
            "key-value-list",
            "table",
            "metric",
            "chart",
            "button",
            "select",
            "badge-row",
            "empty-state",
            "loading",
        ];
        let t = node["type"].as_str().expect("type");
        assert!(RENDERED.contains(&t), "unrendered type {t}");
        if let Some(kids) = node["children"].as_array() {
            assert!(kids.len() <= 50, "over maxChildren");
            kids.iter().for_each(assert_known_types);
        }
    }

    #[test]
    fn lists_exactly_the_given_names_in_order() {
        let names = ["Personal", "Business", "Retirement"];
        let doc = parse(&portfolio_list(&names));
        assert_known_types(&doc);
        assert_eq!(
            children(&doc)[0],
            json!({"type": "heading", "level": 1, "text": "Hello, World!"})
        );
        assert_eq!(table_names(&doc), names);
        assert_eq!(
            children(&doc)[1]["content"],
            "You have 3 portfolios in this workspace."
        );
        assert!(children(&doc).iter().all(|c| c["type"] != "empty-state"));
    }

    #[test]
    fn singular_summary_for_one_portfolio() {
        let doc = parse(&portfolio_list(&["Solo"]));
        assert_eq!(
            children(&doc)[1]["content"],
            "You have 1 portfolio in this workspace."
        );
        assert_eq!(table_names(&doc), ["Solo"]);
    }

    #[test]
    fn empty_workspace_shows_empty_state_and_no_table() {
        let none: [&str; 0] = [];
        let doc = parse(&portfolio_list(&none));
        assert_known_types(&doc);
        let kids = children(&doc);
        assert_eq!(kids.len(), 2);
        assert_eq!(kids[1]["type"], "empty-state");
        assert_eq!(kids[1]["title"], "No portfolios yet");
        assert!(kids.iter().all(|c| c["type"] != "table"));
    }

    #[test]
    fn unavailable_shows_empty_state() {
        let doc = parse(&names_unavailable());
        assert_known_types(&doc);
        let kids = children(&doc);
        assert_eq!(kids[1]["type"], "empty-state");
        assert_eq!(kids[1]["title"], "Portfolio names unavailable");
    }

    #[test]
    fn hostile_names_round_trip_exactly() {
        let names = [
            "quote \" and backslash \\",
            "line\nbreak\ttab\r",
            "ctl \u{0}\u{1}\u{8}\u{c}\u{1f}",
            "unicode 日本語 € 🚀",
            "</script><b>not html</b>",
            "",
        ];
        let doc = parse(&portfolio_list(&names));
        assert_eq!(table_names(&doc), names);
    }

    #[test]
    fn caps_rows_at_the_renderer_limit_and_says_so() {
        let names: Vec<String> = (0..MAX_TABLE_ROWS + 7).map(|i| i.to_string()).collect();
        let doc = parse(&portfolio_list(&names));
        assert_known_types(&doc);
        let listed = table_names(&doc);
        assert_eq!(listed.len(), MAX_TABLE_ROWS);
        assert_eq!(listed, names[..MAX_TABLE_ROWS]);
        let last = children(&doc).last().unwrap();
        assert_eq!(last["content"], "Showing the first 500 of 507 portfolios.");
    }

    #[test]
    fn exactly_the_limit_has_no_truncation_note() {
        let names: Vec<String> = (0..MAX_TABLE_ROWS).map(|i| i.to_string()).collect();
        let doc = parse(&portfolio_list(&names));
        assert_eq!(children(&doc).last().unwrap()["type"], "table");
    }

    #[test]
    fn push_usize_formats_decimal() {
        for n in [0usize, 7, 10, 500, 1234567890, usize::MAX] {
            let mut s = String::new();
            push_usize(&mut s, n);
            assert_eq!(s, n.to_string());
        }
    }
}
