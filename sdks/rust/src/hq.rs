// SPDX-License-Identifier: Apache-2.0
//
//! `hq::*` — the ergonomic, stable author API over the raw generated bindings.
//!
//! Every function maps 1:1 onto a `hellohq:plugin/*` import. Reads return
//! `Result<…, ApiError>`; the host permission-gates each call and surfaces a
//! denial as an [`ApiError`] with a stable `code` (e.g. `"permission-denied"`).
//!
//! The host only sees an import if the plugin actually calls it: `wasm-tools
//! component new` tree-shakes unused interfaces out of the built component, so
//! a plugin that touches only `storage` imports only `storage` (+ `types`).

use alloc::string::String;
use alloc::vec::Vec;

use crate::bindings::hellohq::plugin as raw;

pub use crate::{
    AggregatedSummary, ApiError, AssetCount, CategoryCount, CategoryTotal, ChatMessage,
    CurrencyRate, InferenceOpts, PluginEvent, PortfolioName, SheetInfo, SheetSummary,
};

/// Read-only, permission-gated workspace data. Each function requires the
/// matching manifest permission; `read_aggregated_values` additionally requires
/// Verified trust + per-portfolio scope.
pub mod workspace {
    use super::*;

    /// Every portfolio's id + display name. (`read:portfolio_names`)
    pub fn read_portfolio_names() -> Result<Vec<PortfolioName>, ApiError> {
        raw::workspace::read_portfolio_names()
    }

    /// Sheet/section names for a portfolio (never values). (`read:sheet_structure`)
    pub fn read_sheet_structure(portfolio_id: &str) -> Result<SheetSummary, ApiError> {
        raw::workspace::read_sheet_structure(portfolio_id)
    }

    /// Item counts split by category for a portfolio. (`read:asset_count`)
    pub fn read_asset_count(portfolio_id: &str) -> Result<AssetCount, ApiError> {
        raw::workspace::read_asset_count(portfolio_id)
    }

    /// Workspace currencies and exchange rates. (`read:currency_rates`)
    pub fn read_currency_rates() -> Result<Vec<CurrencyRate>, ApiError> {
        raw::workspace::read_currency_rates()
    }

    /// Per-category portfolio totals. (`read:aggregated_values`, Verified tier)
    pub fn read_aggregated_values(portfolio_id: &str) -> Result<AggregatedSummary, ApiError> {
        raw::workspace::read_aggregated_values(portfolio_id)
    }
}

/// Per-plugin key-value storage. Host-keyed by `(plugin_id, key)`; the host
/// enforces a per-plugin quota.
pub mod storage {
    use super::*;

    /// Read a value. `Ok(None)` if the key is absent.
    pub fn get(key: &str) -> Result<Option<Vec<u8>>, ApiError> {
        raw::storage::get(key)
    }

    /// Write a value (overwrites).
    pub fn set(key: &str, value: &[u8]) -> Result<(), ApiError> {
        raw::storage::set(key, value)
    }

    /// Delete a key (no-op if absent).
    pub fn delete(key: &str) -> Result<(), ApiError> {
        raw::storage::delete(key)
    }

    /// Delete all of this plugin's keys.
    pub fn clear() -> Result<(), ApiError> {
        raw::storage::clear()
    }

    /// List all keys this plugin has stored (keys only, no values).
    pub fn list_keys() -> Result<Vec<String>, ApiError> {
        raw::storage::list_keys()
    }
}

/// Push events from the plugin to the host (size + rate capped host-side).
pub mod events {
    use super::*;

    /// Emit an event. `kind` is a stable tag; `payload` is opaque bytes.
    pub fn emit(kind: &str, payload: &[u8]) -> Result<(), ApiError> {
        raw::events::emit(&PluginEvent {
            kind: String::from(kind),
            payload: payload.to_vec(),
        })
    }

    /// Emit a pre-built [`PluginEvent`].
    pub fn emit_event(event: &PluginEvent) -> Result<(), ApiError> {
        raw::events::emit(event)
    }
}

/// Structured logging. Always available, never permission-gated.
pub mod log {
    use super::*;
    use crate::LogLevel;

    /// Log at an explicit level.
    pub fn write(level: LogLevel, message: &str) {
        raw::log::write(level, message);
    }

    pub fn trace(message: &str) {
        raw::log::write(LogLevel::Trace, message);
    }
    pub fn debug(message: &str) {
        raw::log::write(LogLevel::Debug, message);
    }
    pub fn info(message: &str) {
        raw::log::write(LogLevel::Info, message);
    }
    pub fn warn(message: &str) {
        raw::log::write(LogLevel::Warn, message);
    }
    pub fn error(message: &str) {
        raw::log::write(LogLevel::Error, message);
    }
}

/// AI inference, routed through the host's gated HQAuthProxy. `complete`
/// **streams** token deltas.
///
/// The plugin never holds a key or talks to a provider directly. The returned
/// stream yields one UTF-8 token delta per element; concatenate them for the
/// full completion.
///
/// ## Async-only drain
///
/// Draining the stream yields, so it must run inside an `async` context. The
/// canonical `guest.run` export is sync — it can *start* a completion but
/// cannot drain it. For streaming end to end, build against the
/// `inference-quickstart` world (`wit/quickstart.wit`), whose `run` is an
/// `async func`, and use [`collect`] to gather the deltas:
///
/// ```ignore
/// // inside an async export `run`:
/// let stream = hq::inference::complete(&messages, opts)?;
/// let text: String = hq::inference::collect(stream).await;
/// ```
pub mod inference {
    use super::*;

    /// The token-delta stream returned by [`complete`]. Re-exported from
    /// wit-bindgen's async runtime; each element is one UTF-8 token delta.
    pub use wit_bindgen::rt::async_support::StreamReader;

    /// Build a `user`-role message.
    pub fn user(content: impl Into<String>) -> ChatMessage {
        ChatMessage { role: String::from("user"), content: content.into() }
    }

    /// Build a `system`-role message.
    pub fn system(content: impl Into<String>) -> ChatMessage {
        ChatMessage { role: String::from("system"), content: content.into() }
    }

    /// Build an `assistant`-role message.
    pub fn assistant(content: impl Into<String>) -> ChatMessage {
        ChatMessage { role: String::from("assistant"), content: content.into() }
    }

    /// Start a streaming completion. Returns a [`StreamReader`] of token deltas
    /// on success, or an [`ApiError`] on gate denial / validation failure.
    ///
    /// Drain the stream in an async context (see the module docs and
    /// [`collect`]).
    pub fn complete(
        messages: &[ChatMessage],
        opts: InferenceOpts,
    ) -> Result<StreamReader<String>, ApiError> {
        raw::inference::complete(messages, opts)
    }

    /// Drain a token-delta stream to a single `String`, concatenating each
    /// delta. Must be awaited inside an `async` export.
    pub async fn collect(stream: StreamReader<String>) -> String {
        let deltas: Vec<String> = stream.collect().await;
        let mut out = String::new();
        for d in deltas {
            out.push_str(&d);
        }
        out
    }
}
