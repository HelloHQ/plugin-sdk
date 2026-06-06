//! HelloHQ Tier 2 (Wasm) plugin SDK for Rust.
//!
//! Write a HelloHQ plugin without touching raw pointers. The [`plugin!`] macro
//! wires the linear-memory ABI the host actually calls; [`host`] gives typed,
//! permission-gated data reads; [`ui`] builds the declarative UI tree the host
//! renders natively.
//!
//! ```ignore
//! use hellohq_plugin_sdk::{plugin, host, ui, PluginError};
//!
//! plugin! {
//!     fn run(_input: &[u8]) -> Result<Vec<u8>, PluginError> {
//!         let portfolios = host::read_portfolio_names().unwrap_or_default();
//!         let items = portfolios.iter().enumerate()
//!             .map(|(i, p)| (format!("Portfolio {}", i + 1), p.name.clone()))
//!             .collect();
//!         Ok(ui::column(vec![
//!             ui::heading(&format!("Portfolios ({})", portfolios.len())),
//!             ui::key_value_list(items),
//!         ]).to_bytes())
//!     }
//! }
//! ```
//!
//! ## ABI (matches the host's `PluginWasmService`)
//! - Exports: `memory`, `alloc(i32) -> i32`, `run(i32, i32) -> i64`
//!   (packed `(ptr << 32) | len`).
//! - Imports: `env.hq_read(i32, i32) -> i64`, `env.emit_event(i32,i32,i32,i32)`.
//!
//! Build for the host:
//! ```bash
//! cargo build --target wasm32-unknown-unknown --release
//! ```

#![allow(clippy::missing_safety_doc)]

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// The protocol version this SDK targets (see `HelloHQ/plugin-protocol`).
pub const PROTOCOL_VERSION: &str = "1.0.0";

// ─────────────────────────────────────────────────────────────────────────────
// Linear-memory ABI
// ─────────────────────────────────────────────────────────────────────────────

// Host imports. `hq_read` answers a JSON data request; `emit_event` pushes an
// event to the plugin's WebView (no-op in declarative/headless mode).
//
// Only real on wasm32; native builds (the SDK's own `cargo test`) get stubs so
// the crate links — the host functions are unreachable off-target.
#[cfg(target_arch = "wasm32")]
#[link(wasm_import_module = "env")]
extern "C" {
    fn hq_read(req_ptr: i32, req_len: i32) -> i64;
    fn emit_event(name_ptr: i32, name_len: i32, payload_ptr: i32, payload_len: i32);
}

#[cfg(not(target_arch = "wasm32"))]
unsafe fn hq_read(_req_ptr: i32, _req_len: i32) -> i64 {
    panic!("hq_read is only available inside the Wasm host");
}

#[cfg(not(target_arch = "wasm32"))]
unsafe fn emit_event(_: i32, _: i32, _: i32, _: i32) {
    panic!("emit_event is only available inside the Wasm host");
}

fn alloc_impl(len: i32) -> i32 {
    let mut buf = Vec::<u8>::with_capacity(len.max(0) as usize);
    let ptr = buf.as_mut_ptr() as i32;
    core::mem::forget(buf);
    ptr
}

/// Allocate `len` bytes the host can write into; returns the pointer.
///
/// A leaking bump-style allocator: the module instance is one-shot per run, so
/// buffers are reclaimed when the instance is dropped. The host calls this to
/// place `run`'s input and each `hq_read` response. Exported only on wasm.
#[cfg(target_arch = "wasm32")]
#[no_mangle]
pub extern "C" fn alloc(len: i32) -> i32 {
    alloc_impl(len)
}

/// Pack a `(ptr, len)` pair into the `i64` the host reads back.
pub fn pack(ptr: i32, len: i32) -> i64 {
    (((ptr as u64) << 32) | (len as u32 as u64)) as i64
}

fn read_mem(ptr: i32, len: i32) -> Vec<u8> {
    if ptr <= 0 || len <= 0 {
        return Vec::new();
    }
    unsafe { core::slice::from_raw_parts(ptr as *const u8, len as usize).to_vec() }
}

fn write_mem(bytes: &[u8]) -> i32 {
    let ptr = alloc_impl(bytes.len() as i32);
    unsafe {
        core::ptr::copy_nonoverlapping(bytes.as_ptr(), ptr as *mut u8, bytes.len());
    }
    ptr
}

// ─────────────────────────────────────────────────────────────────────────────
// Errors
// ─────────────────────────────────────────────────────────────────────────────

/// Error a plugin can return from `run`.
#[derive(Debug, Clone)]
pub enum PluginError {
    InvalidInput(String),
    ExecutionFailed(String),
    UnsupportedFunction(String),
}

impl core::fmt::Display for PluginError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            PluginError::InvalidInput(m) => write!(f, "invalid input: {m}"),
            PluginError::ExecutionFailed(m) => write!(f, "execution failed: {m}"),
            PluginError::UnsupportedFunction(m) => write!(f, "unsupported function: {m}"),
        }
    }
}

/// Error returned by a [`host`] data read.
#[derive(Debug, Clone)]
pub enum HostError {
    /// The permission gate denied the read (`error: "denied:<perm>"`).
    Denied(String),
    /// The host did not recognise the request method.
    Unknown(String),
    /// Malformed response, or the data did not match the expected shape.
    Decode(String),
}

impl core::fmt::Display for HostError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            HostError::Denied(p) => write!(f, "permission denied: {p}"),
            HostError::Unknown(m) => write!(f, "unknown host method: {m}"),
            HostError::Decode(m) => write!(f, "decode error: {m}"),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Host API — typed, permission-gated reads over the `hq_read` JSON protocol
// ─────────────────────────────────────────────────────────────────────────────

/// A portfolio id + display name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioName {
    pub id: String,
    pub name: String,
}

/// A workspace currency and its exchange rate.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CurrencyRate {
    pub id: String,
    pub name: String,
    pub symbol: String,
    pub rate: f64,
}

/// Typed host reads. Each requires the matching manifest permission; a denied
/// read returns [`HostError::Denied`]. Data shapes mirror the host's
/// `PluginSyncReader` JSON.
pub mod host {
    use super::*;

    /// Low-level escape hatch: send a raw request and get the `data` value back.
    pub fn read_raw(method: &str, portfolio_id: Option<&str>) -> Result<Value, HostError> {
        let mut req = json!({ "method": method });
        if let Some(pid) = portfolio_id {
            req["portfolio_id"] = json!(pid);
        }
        let bytes = serde_json::to_vec(&req).unwrap();
        let req_ptr = write_mem(&bytes);
        let packed = unsafe { hq_read(req_ptr, bytes.len() as i32) };
        let resp_ptr = ((packed >> 32) & 0xFFFF_FFFF) as i32;
        let resp_len = (packed & 0xFFFF_FFFF) as i32;
        let resp = read_mem(resp_ptr, resp_len);

        let v: Value =
            serde_json::from_slice(&resp).map_err(|e| HostError::Decode(e.to_string()))?;
        if v.get("ok").and_then(Value::as_bool) == Some(true) {
            Ok(v.get("data").cloned().unwrap_or(Value::Null))
        } else {
            let err = v.get("error").and_then(Value::as_str).unwrap_or("error");
            if let Some(perm) = err.strip_prefix("denied:") {
                Err(HostError::Denied(perm.to_string()))
            } else if let Some(m) = err.strip_prefix("unknown_method:") {
                Err(HostError::Unknown(m.to_string()))
            } else {
                Err(HostError::Decode(err.to_string()))
            }
        }
    }

    fn read_typed<T: DeserializeOwned>(
        method: &str,
        portfolio_id: Option<&str>,
    ) -> Result<T, HostError> {
        let data = read_raw(method, portfolio_id)?;
        serde_json::from_value(data).map_err(|e| HostError::Decode(e.to_string()))
    }

    /// `read:portfolio_names` — every portfolio's id + name.
    pub fn read_portfolio_names() -> Result<Vec<PortfolioName>, HostError> {
        read_typed("read:portfolio_names", None)
    }

    /// `read:sheet_structure` — sheet/section/item names (never values).
    /// Pass a `portfolio_id` to scope to one portfolio.
    pub fn read_sheet_structure(portfolio_id: Option<&str>) -> Result<Value, HostError> {
        read_raw("read:sheet_structure", portfolio_id)
    }

    /// `read:asset_count` — item counts split by asset/debt.
    pub fn read_asset_count(portfolio_id: Option<&str>) -> Result<Value, HostError> {
        read_raw("read:asset_count", portfolio_id)
    }

    /// `read:currency_rates` — workspace currencies and rates.
    pub fn read_currency_rates() -> Result<Vec<CurrencyRate>, HostError> {
        read_typed("read:currency_rates", None)
    }

    /// `read:aggregated_values` — per-currency portfolio totals (Verified tier).
    pub fn read_aggregated_values(portfolio_id: Option<&str>) -> Result<Value, HostError> {
        read_raw("read:aggregated_values", portfolio_id)
    }

    /// Push an event to the plugin's WebView. No-op in declarative/headless mode.
    pub fn emit(name: &str, payload: &[u8]) {
        let name_ptr = write_mem(name.as_bytes());
        let payload_ptr = write_mem(payload);
        unsafe {
            emit_event(name_ptr, name.len() as i32, payload_ptr, payload.len() as i32);
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Declarative UI builder — the 15 component types the host renders
// ─────────────────────────────────────────────────────────────────────────────

/// A declarative UI node. Build trees with the [`ui`] helpers and call
/// [`View::to_bytes`] to return them from `run`.
#[derive(Debug, Clone)]
pub struct View(pub Value);

impl View {
    /// Serialize to the JSON bytes the host expects from `run`.
    pub fn to_bytes(&self) -> Vec<u8> {
        serde_json::to_vec(&self.0).unwrap()
    }
    /// The underlying JSON value.
    pub fn into_value(self) -> Value {
        self.0
    }
}

/// Builders for the declarative component schema (see
/// `docs/plugin/04_ui-declarative.md`).
pub mod ui {
    use super::*;

    fn container(children: Vec<View>, kind: &str) -> View {
        View(json!({
            "type": kind,
            "children": children.into_iter().map(|c| c.0).collect::<Vec<_>>(),
        }))
    }

    pub fn column(children: Vec<View>) -> View {
        container(children, "column")
    }
    pub fn row(children: Vec<View>) -> View {
        container(children, "row")
    }
    pub fn section(title: &str, children: Vec<View>) -> View {
        View(json!({
            "type": "section",
            "title": title,
            "children": children.into_iter().map(|c| c.0).collect::<Vec<_>>(),
        }))
    }
    pub fn heading(text: &str) -> View {
        View(json!({ "type": "heading", "text": text }))
    }
    pub fn text(s: &str) -> View {
        View(json!({ "type": "text", "text": s }))
    }
    pub fn divider() -> View {
        View(json!({ "type": "divider" }))
    }
    pub fn loading() -> View {
        View(json!({ "type": "loading" }))
    }

    /// A label→value list. `items` is `(label, value)` pairs.
    pub fn key_value_list(items: Vec<(String, String)>) -> View {
        let items: Vec<Value> = items
            .into_iter()
            .map(|(label, value)| json!({ "label": label, "value": value }))
            .collect();
        View(json!({ "type": "key-value-list", "items": items }))
    }

    /// A metric tile. `delta` is an optional signed change string.
    pub fn metric(label: &str, value: &str, delta: Option<&str>) -> View {
        let mut v = json!({ "type": "metric", "label": label, "value": value });
        if let Some(d) = delta {
            v["delta"] = json!(d);
        }
        View(v)
    }

    /// A table. `columns` are header labels; `rows` are cell-string rows.
    pub fn table(columns: Vec<&str>, rows: Vec<Vec<String>>) -> View {
        View(json!({
            "type": "table",
            "columns": columns.iter().map(|c| json!({ "label": c })).collect::<Vec<_>>(),
            "rows": rows,
        }))
    }

    /// A button. `on_tap` is `(function, args)` dispatched back to `run`.
    pub fn button(label: &str, function: &str, args: Value) -> View {
        View(json!({
            "type": "button",
            "label": label,
            "on-tap": { "function": function, "args": args },
        }))
    }

    /// A dropdown. `options` are `(value, label)`; selection re-invokes `run`
    /// with `selected_value` injected into `args`.
    pub fn select(options: Vec<(String, String)>, function: &str) -> View {
        let options: Vec<Value> = options
            .into_iter()
            .map(|(value, label)| json!({ "value": value, "label": label }))
            .collect();
        View(json!({
            "type": "select",
            "options": options,
            "action": { "function": function, "args": {} },
        }))
    }

    /// A row of coloured badges. `badges` is `(label, color)`.
    pub fn badge_row(badges: Vec<(String, String)>) -> View {
        let badges: Vec<Value> = badges
            .into_iter()
            .map(|(label, color)| json!({ "label": label, "color": color }))
            .collect();
        View(json!({ "type": "badge-row", "badges": badges }))
    }

    pub fn empty_state(title: &str, description: Option<&str>) -> View {
        let mut v = json!({ "type": "empty-state", "title": title });
        if let Some(d) = description {
            v["description"] = json!(d);
        }
        View(v)
    }

    /// A chart. `kind` is e.g. "line"/"bar"; `series` is host-schema JSON.
    pub fn chart(kind: &str, series: Value) -> View {
        View(json!({ "type": "chart", "chart_type": kind, "series": series }))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Entry-point macro
// ─────────────────────────────────────────────────────────────────────────────

/// Wire a safe `run(&[u8]) -> Result<Vec<u8>, PluginError>` into the host ABI.
///
/// Generates the exported `run(ptr, len) -> i64`: reads the input from guest
/// memory, calls your function, writes the output, returns the packed pointer.
/// On error it returns a declarative `empty-state` describing the failure so the
/// pane degrades gracefully instead of trapping.
#[macro_export]
macro_rules! plugin {
    (fn run($input:ident: &[u8]) -> Result<Vec<u8>, $err:ty> $body:block) => {
        #[no_mangle]
        pub extern "C" fn run(ptr: i32, len: i32) -> i64 {
            // The author's body becomes a plain fn so the `block` metavariable
            // sits in fn-body position (closures with a return type won't accept
            // a metavariable there directly).
            fn __hq_run($input: &[u8]) -> ::core::result::Result<::std::vec::Vec<u8>, $err> $body

            let input: &[u8] = if ptr <= 0 || len <= 0 {
                &[]
            } else {
                unsafe { ::core::slice::from_raw_parts(ptr as *const u8, len as usize) }
            };
            let out = match __hq_run(input) {
                Ok(bytes) => bytes,
                Err(e) => $crate::ui::empty_state(
                    "This plugin could not run.",
                    Some(&::std::format!("{}", e)),
                )
                .to_bytes(),
            };
            let p = $crate::write_mem_public(&out);
            $crate::pack(p, out.len() as i32)
        }
    };
}

/// Public shim so the [`plugin!`] macro (expanded in the author's crate) can
/// place bytes in guest memory. Not intended for direct use.
#[doc(hidden)]
pub fn write_mem_public(bytes: &[u8]) -> i32 {
    write_mem(bytes)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests (host-independent: marshalling + UI builders)
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pack_round_trips() {
        let packed = pack(0x1234, 0x56);
        assert_eq!(((packed >> 32) & 0xFFFF_FFFF) as i32, 0x1234);
        assert_eq!((packed & 0xFFFF_FFFF) as i32, 0x56);
    }

    #[test]
    fn column_builds_expected_tree() {
        let v = ui::column(vec![ui::heading("Hi"), ui::text("there")]);
        let s = serde_json::to_string(&v.0).unwrap();
        assert!(s.contains("\"type\":\"column\""));
        assert!(s.contains("\"type\":\"heading\""));
        assert!(s.contains("\"text\":\"there\""));
    }

    #[test]
    fn key_value_list_shape() {
        let v = ui::key_value_list(vec![("A".into(), "1".into())]);
        let s = serde_json::to_string(&v.0).unwrap();
        assert!(s.contains("\"type\":\"key-value-list\""));
        assert!(s.contains("\"label\":\"A\""));
        assert!(s.contains("\"value\":\"1\""));
    }

    #[test]
    fn button_action_shape() {
        let v = ui::button("Go", "refresh", json!({"x": 1}));
        let s = serde_json::to_string(&v.0).unwrap();
        assert!(s.contains("\"on-tap\""));
        assert!(s.contains("\"function\":\"refresh\""));
    }
}
