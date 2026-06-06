//! HelloHQ Tier 2 (Wasm) plugin SDK for Rust.
//!
//! This crate provides the idiomatic surface plugin authors use; the raw host
//! imports are generated from the WIT in `HelloHQ/plugin-protocol` via
//! `wit-bindgen` (see `abi/`). Until generated bindings are wired in, the host
//! imports below are declared as the linear-memory ABI the host actually calls.
//!
//! Minimal plugin:
//! ```ignore
//! use hellohq_plugin_sdk::{plugin, PluginError};
//!
//! plugin! {
//!     fn run(input: &[u8]) -> Result<Vec<u8>, PluginError> {
//!         Ok(b"{\"type\":\"text\",\"content\":\"hello\"}".to_vec())
//!     }
//! }
//! ```

#![allow(clippy::missing_safety_doc)]

use serde::{Deserialize, Serialize};

/// The protocol version this SDK targets.
pub const PROTOCOL_VERSION: &str = "1.0.0";

// ── Domain types (mirror hellohq:plugin/types) ──────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioName {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AggregatedSummary {
    pub portfolio_id: String,
    pub currency: String,
    pub total_value: f64,
    pub as_of_timestamp: u64,
}

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

// ── Linear-memory ABI ───────────────────────────────────────────────────────
//
// The host calls `hq_plugin_run(ptr, len) -> packed(ptr<<32 | len)` after
// copying the input JSON into memory it obtained from `hq_alloc`. The
// `plugin!` macro wires a safe `run` into these exports.

/// Allocate `len` bytes the host can write into. Returns the pointer.
#[no_mangle]
pub extern "C" fn hq_alloc(len: usize) -> *mut u8 {
    let mut buf = Vec::<u8>::with_capacity(len);
    let ptr = buf.as_mut_ptr();
    core::mem::forget(buf);
    ptr
}

/// Free a buffer previously returned to the host.
///
/// # Safety
/// `ptr`/`len` must come from a prior `hq_alloc`/run return.
#[no_mangle]
pub unsafe extern "C" fn hq_free(ptr: *mut u8, len: usize) {
    if !ptr.is_null() && len > 0 {
        drop(Vec::from_raw_parts(ptr, 0, len));
    }
}

/// Pack a pointer and length into the i64 the host expects back from `run`.
pub fn pack(ptr: *const u8, len: usize) -> i64 {
    (((ptr as u64) << 32) | (len as u64)) as i64
}

/// Implement the plugin entry points. See crate docs for usage.
#[macro_export]
macro_rules! plugin {
    (fn run($input:ident: &[u8]) -> Result<Vec<u8>, $err:ty> $body:block) => {
        #[no_mangle]
        pub extern "C" fn hq_plugin_run(ptr: *mut u8, len: usize) -> i64 {
            let $input: &[u8] =
                unsafe { ::core::slice::from_raw_parts(ptr, len) };
            let run = |$input: &[u8]| -> Result<Vec<u8>, $err> $body;
            let out = match run($input) {
                Ok(bytes) => bytes,
                Err(e) => format!("{{\"error\":\"{}\"}}", e).into_bytes(),
            };
            let mut out = out.into_boxed_slice();
            let p = out.as_mut_ptr();
            let l = out.len();
            ::core::mem::forget(out);
            $crate::pack(p, l)
        }
    };
}
