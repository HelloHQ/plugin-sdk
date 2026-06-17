// SPDX-License-Identifier: Apache-2.0
//
//! HelloHQ Tier-2 **compute** component for the Portfolio Overview example.
//!
//! This is the *logic* half of a WebView plugin: a headless
//! `hellohq:plugin@0.1.0` component built with `hellohq-plugin-sdk`. The
//! *presentation* half lives in `ui/` (a framework-agnostic WebView bundle) and
//! talks to this component through the host bridge — see this example's README.
//!
//! On `run` the component reads the workspace portfolio names
//! (`hq::workspace::read_portfolio_names`, permission-gated) and returns a
//! compact JSON document the UI renders:
//!
//! ```json
//! { "count": 2, "denied": false, "portfolios": [ { "id": "ptf_a", "name": "Growth" }, … ] }
//! ```
//!
//! The UI invokes this via `host.compute("overview", {})` (the bridge `compute`
//! action), which runs `run({"function":"overview","args":{}})`. A denied read
//! degrades gracefully to `{ "count": 0, "portfolios": [], "denied": true }`
//! rather than failing the call.
//!
//! It touches only `workspace` + `log`, so `wasm-tools component new`
//! tree-shakes everything else (storage/events/inference) out of the built
//! component — keeping the import surface (and the granted-permission set)
//! minimal.

#![no_std]

extern crate alloc;

use alloc::string::{String, ToString};
use alloc::vec::Vec;

use hellohq_plugin_sdk::{export_plugin, hq, Plugin, PluginMetadata, PortfolioName};

// dlmalloc global allocator + trapping panic handler, so the built component
// imports only `hellohq:plugin/*` (no `wasi:*`).
hellohq_plugin_sdk::setup_guest!();

struct PortfolioOverview;

impl Plugin for PortfolioOverview {
    fn init() {
        hq::log::info("portfolio-overview: init");
    }

    fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
        hq::log::debug("portfolio-overview: run");

        // Permission-gated read. If `read:portfolio_names` was not granted the
        // host returns a denial; degrade gracefully to an empty, flagged result
        // so the UI can show its own empty state instead of an error.
        match hq::workspace::read_portfolio_names() {
            Ok(portfolios) => Ok(encode_overview(&portfolios, false)),
            Err(e) => {
                hq::log::warn(&e.message);
                Ok(encode_overview(&[], true))
            }
        }
    }

    fn metadata() -> PluginMetadata {
        PluginMetadata {
            id: String::from("com.hellohq.portfolio-overview"),
            version: String::from("1.0.0"),
        }
    }
}

/// Serialize the overview document by hand (no serde dependency — keeps the
/// no_std component tiny). Names are JSON-string-escaped.
fn encode_overview(portfolios: &[PortfolioName], denied: bool) -> Vec<u8> {
    let mut out = String::from("{\"count\":");
    out.push_str(&portfolios.len().to_string());
    out.push_str(",\"denied\":");
    out.push_str(if denied { "true" } else { "false" });
    out.push_str(",\"portfolios\":[");
    for (i, p) in portfolios.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        out.push_str("{\"id\":\"");
        push_escaped(&mut out, &p.id);
        out.push_str("\",\"name\":\"");
        push_escaped(&mut out, &p.name);
        out.push_str("\"}");
    }
    out.push_str("]}");
    out.into_bytes()
}

/// Append `s` to `out` with the minimal JSON string escaping (quotes,
/// backslashes, and control characters).
fn push_escaped(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                // \u00XX for the remaining control characters.
                out.push_str("\\u00");
                let byte = c as u8;
                out.push(hex_nibble(byte >> 4));
                out.push(hex_nibble(byte & 0x0f));
            }
            c => out.push(c),
        }
    }
}

fn hex_nibble(n: u8) -> char {
    match n {
        0..=9 => (b'0' + n) as char,
        _ => (b'a' + (n - 10)) as char,
    }
}

export_plugin!(PortfolioOverview);
