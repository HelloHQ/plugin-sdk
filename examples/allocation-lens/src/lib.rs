// SPDX-License-Identifier: Apache-2.0
//
//! HelloHQ Tier-2 **compute** component for the Allocation Lens example.
//!
//! This is the *logic* half of a WebView plugin: a headless streaming-inference
//! component built against the SDK's `inference-quickstart` world
//! (`../../sdks/rust/wit`), whose `run` is an `async func` — required because
//! draining `inference.complete`'s `stream<string>` yields. The *presentation*
//! half lives in `ui/` (a Svelte WebView bundle) and talks to this component
//! through the host bridge — see this example's README.
//!
//! The UI does the workspace reads itself (portfolio names + asset counts via
//! the host bridge `read` action — no Wasm needed) and hands this component a
//! pre-built `context` string. On `run` the component parses its input JSON
//! `{"function":"narrate","args":{"context":"<text>"}}`, builds a system +
//! user `ChatMessage` pair, calls `inference::complete`, drains the token-delta
//! stream, and returns JSON bytes `{"narrative":"<text>"}`.
//!
//! The context never contains raw account values — only portfolio names and
//! per-category item counts — and the system prompt instructs the model never
//! to reveal raw account numbers. On a `complete` error `run` returns
//! `Err(message)`.
//!
//! It imports only `hellohq:plugin/{types, log, inference}`; `wasm-tools
//! component new` keeps it free of any `wasi:*`, workspace, storage, or events
//! import.

#![no_std]

extern crate alloc;

use alloc::string::String;
use alloc::vec;
use alloc::vec::Vec;

#[global_allocator]
static ALLOC: dlmalloc::GlobalDlmalloc = dlmalloc::GlobalDlmalloc;

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

wit_bindgen::generate!({
    path: "../../sdks/rust/wit",
    world: "inference-quickstart",
    generate_all,
});

use hellohq::plugin::inference::{complete, ChatMessage, InferenceOpts};
use hellohq::plugin::log::{write as log_write, Level};

const SYSTEM_PROMPT: &str = "You are a concise, plain-English financial analyst. \
Given a portfolio's structure (portfolio names and per-category item counts only), \
describe in 2-3 sentences what the allocation shape implies about the investor's \
position and flag any concentration concern. You are NEVER given and must NEVER \
reveal, invent, or imply raw account numbers, balances, or monetary values — \
reason only about the counts and structure provided.";

struct Component;

impl Guest for Component {
    // Async because we drain the returned `stream<string>`.
    async fn run(input: Vec<u8>) -> Result<Vec<u8>, String> {
        log_write(Level::Info, "allocation-lens: run");

        // Manual, no_std JSON extraction of args.context — avoids pulling in a
        // serde dependency for a single string field.
        let input_str = match core::str::from_utf8(&input) {
            Ok(s) => s,
            Err(_) => return Err(String::from("input was not valid UTF-8")),
        };
        let context = match extract_string_field(input_str, "context") {
            Some(c) => c,
            None => return Err(String::from("missing args.context in input")),
        };

        let messages = vec![
            ChatMessage {
                role: String::from("system"),
                content: String::from(SYSTEM_PROMPT),
            },
            ChatMessage {
                role: String::from("user"),
                content: context,
            },
        ];
        let opts = InferenceOpts { max_tokens: 256, temperature: Some(0.3) };

        let stream = match complete(&messages, opts) {
            Ok(s) => s,
            Err(e) => return Err(e.message),
        };

        // Drain token deltas, concatenating.
        let deltas: Vec<String> = stream.collect().await;
        let mut narrative = String::new();
        for d in deltas {
            narrative.push_str(&d);
        }

        log_write(Level::Info, "allocation-lens: stream drained");
        Ok(encode_narrative(&narrative))
    }
}

/// Serialize `{"narrative":"<text>"}` by hand (no serde — keeps the no_std
/// component tiny). The narrative is JSON-string-escaped.
fn encode_narrative(narrative: &str) -> Vec<u8> {
    let mut out = String::from("{\"narrative\":\"");
    push_escaped(&mut out, narrative);
    out.push_str("\"}");
    out.into_bytes()
}

/// Append `s` to `out` with minimal JSON string escaping.
fn push_escaped(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
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

/// Extract a top-level-or-nested JSON string field by key from `json`, decoding
/// the common escape sequences. Minimal hand-rolled scanner: finds the first
/// `"<key>"` token, skips to the `:` and the opening quote of its string value,
/// then reads to the closing quote honoring backslash escapes. Returns `None`
/// if the key is absent or its value is not a string.
fn extract_string_field(json: &str, key: &str) -> Option<String> {
    let bytes = json.as_bytes();
    let needle = {
        let mut k = String::from("\"");
        k.push_str(key);
        k.push('"');
        k
    };
    let key_pos = find_sub(json, &needle)?;
    let mut i = key_pos + needle.len();

    // Skip whitespace, expect ':'.
    while i < bytes.len() && bytes[i].is_ascii_whitespace() {
        i += 1;
    }
    if i >= bytes.len() || bytes[i] != b':' {
        return None;
    }
    i += 1;
    while i < bytes.len() && bytes[i].is_ascii_whitespace() {
        i += 1;
    }
    if i >= bytes.len() || bytes[i] != b'"' {
        return None;
    }
    i += 1; // past opening quote

    let mut out = String::new();
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'\\' {
            i += 1;
            if i >= bytes.len() {
                return None;
            }
            match bytes[i] {
                b'"' => out.push('"'),
                b'\\' => out.push('\\'),
                b'/' => out.push('/'),
                b'n' => out.push('\n'),
                b'r' => out.push('\r'),
                b't' => out.push('\t'),
                b'b' => out.push('\u{0008}'),
                b'f' => out.push('\u{000C}'),
                b'u' => {
                    // \uXXXX — basic multilingual plane only.
                    if i + 4 >= bytes.len() {
                        return None;
                    }
                    let mut code: u32 = 0;
                    for _ in 0..4 {
                        i += 1;
                        code = (code << 4) | hex_val(bytes[i])? as u32;
                    }
                    if let Some(ch) = char::from_u32(code) {
                        out.push(ch);
                    }
                }
                _ => return None,
            }
            i += 1;
        } else if b == b'"' {
            return Some(out);
        } else {
            // Copy the (possibly multi-byte UTF-8) character starting at i.
            let ch_len = utf8_len(b);
            if i + ch_len > bytes.len() {
                return None;
            }
            if let Ok(s) = core::str::from_utf8(&bytes[i..i + ch_len]) {
                out.push_str(s);
            }
            i += ch_len;
        }
    }
    None
}

fn find_sub(haystack: &str, needle: &str) -> Option<usize> {
    let hb = haystack.as_bytes();
    let nb = needle.as_bytes();
    if nb.is_empty() || nb.len() > hb.len() {
        return None;
    }
    let mut i = 0;
    while i + nb.len() <= hb.len() {
        if &hb[i..i + nb.len()] == nb {
            return Some(i);
        }
        i += 1;
    }
    None
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn utf8_len(first: u8) -> usize {
    if first < 0x80 {
        1
    } else if first >> 5 == 0b110 {
        2
    } else if first >> 4 == 0b1110 {
        3
    } else {
        4
    }
}

export!(Component);
