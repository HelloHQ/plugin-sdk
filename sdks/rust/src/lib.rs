// SPDX-License-Identifier: Apache-2.0
//
//! HelloHQ Tier-2 **Component Model** plugin SDK for Rust.
//!
//! Build a HelloHQ plugin as a WebAssembly **component** against the canonical
//! `hellohq:plugin@0.1.0` WIT (vendored in `wit/`). The host implements and
//! permission-gates every import; an interface absent from the built component
//! (tree-shaken away because the plugin never calls it) is structurally
//! unreachable.
//!
//! This crate supersedes the legacy core-module ABI (raw
//! `wasm32-unknown-unknown` + a `{"method":…}` JSON `hq_read` protocol). There
//! are no consumers of the legacy ABI; this is the path forward.
//!
//! # Writing a plugin
//!
//! ```ignore
//! #![no_std]
//! extern crate alloc;
//! use alloc::{vec::Vec, string::String, format};
//! use hellohq_plugin_sdk::{hq, export_plugin, Plugin, PluginMetadata};
//!
//! // Required once per guest crate: panic handler + global allocator.
//! hellohq_plugin_sdk::setup_guest!();
//!
//! struct MyPlugin;
//!
//! impl Plugin for MyPlugin {
//!     fn init() {
//!         hq::log::info("my-plugin starting");
//!     }
//!
//!     fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
//!         let names = hq::workspace::read_portfolio_names()
//!             .map_err(|e| e.message)?;
//!         hq::storage::set("last-count", &[names.len() as u8])
//!             .map_err(|e| e.message)?;
//!         hq::events::emit("scanned", b"ok").ok();
//!         Ok(format!("{} portfolios", names.len()).into_bytes())
//!     }
//!
//!     fn metadata() -> PluginMetadata {
//!         PluginMetadata { id: "my-plugin".into(), version: "0.1.0".into() }
//!     }
//! }
//!
//! export_plugin!(MyPlugin);
//! ```
//!
//! Build it:
//!
//! ```bash
//! rustup target add wasm32-unknown-unknown
//! cargo build --release --target wasm32-unknown-unknown
//! wasm-tools component new \
//!   target/wasm32-unknown-unknown/release/my_plugin.wasm \
//!   -o my_plugin.component.wasm
//! ```
//!
//! # `no_std`
//!
//! The SDK is `#![no_std]` and re-exports `alloc`. A guest crate provides its
//! own `#[global_allocator]` and `#[panic_handler]`; the [`setup_guest!`] macro
//! wires the proven `dlmalloc` + `wasm32::unreachable()` recipe so the built
//! component imports only `hellohq:plugin/*` (no `wasi:*`).
//!
//! # Streaming inference
//!
//! [`hq::inference::complete`] returns the raw `stream<string>` of token
//! deltas. Draining the stream **yields**, so it must happen inside an `async`
//! context. The canonical `guest` export (`run`) is a plain sync function, so
//! the sync path can start a completion but cannot drain it from `run`. For the
//! full streaming experience, build against the narrower
//! **`inference-quickstart`** world (`wit/quickstart.wit`), whose `run` is an
//! `async func` — see `examples/component-quickstart/INFERENCE.md`. The
//! [`hq::inference::collect`] helper drains a stream to a `String`.

#![no_std]
#![allow(clippy::missing_safety_doc)]

extern crate alloc;

/// The protocol version this SDK targets: the `hellohq:plugin@0.1.0` WIT package
/// it binds against and the Tier-1 sidecar handshake value. Pre-stable (the
/// protocol was reset to 0.1.0 — no consumers yet). Kept in sync across the
/// Python/JS/Go SDKs by CI's `version-consistency` job.
pub const PROTOCOL_VERSION: &str = "0.1.0";

// ─────────────────────────────────────────────────────────────────────────────
// Generated bindings (canonical `hellohq:plugin@0.1.0`, full world)
// ─────────────────────────────────────────────────────────────────────────────
//
// `generate_all` pulls in the transitively-used `types` interface. The streaming
// `inference` import requires wit-bindgen's `async` runtime; the SDK depends on
// wit-bindgen with `features = ["macros", "async"]`, `default-features = false`
// (see Cargo.toml) so the guest stays `no_std`.
//
// `pub` so author crates can reach the raw bindings if they outgrow the
// ergonomic `hq` wrappers (escape hatch). Most plugins never touch this module.
pub mod bindings {
    wit_bindgen::generate!({
        path: "wit",
        world: "hellohq-plugin",
        generate_all,
        // Make the generated export macro public so `export_plugin!` (in the
        // crate root module) can invoke it. `default_bindings_module` points the
        // macro's type lookups back at this module so it resolves correctly when
        // invoked from the author crate.
        pub_export_macro: true,
        default_bindings_module: "hellohq_plugin_sdk::bindings",
    });
}

// Re-export the generated record/error types under clean, stable names so
// authors `use hellohq_plugin_sdk::{ApiError, PortfolioName, …}` rather than
// reaching into `bindings::*`.
pub use bindings::exports::hellohq::plugin::guest::PluginMetadata;
pub use bindings::hellohq::plugin::events::PluginEvent;
pub use bindings::hellohq::plugin::inference::{ChatMessage, InferenceOpts};
pub use bindings::hellohq::plugin::log::Level as LogLevel;
pub use bindings::hellohq::plugin::types::{
    AggregatedSummary, ApiError, AssetCount, CategoryCount, CategoryTotal, CurrencyRate,
    PortfolioName, SheetInfo, SheetSummary,
};

mod plugin;
pub use plugin::Plugin;

pub mod hq;

/// Re-export of `alloc` so the `export_plugin!` macro can reference
/// `$crate::__alloc::{vec::Vec, string::String}` from the author crate (which
/// may not declare its own `extern crate alloc`).
#[doc(hidden)]
pub mod __alloc {
    pub use alloc::string;
    pub use alloc::vec;
}

/// Wire the guest-side runtime requirements for a `wasm32-unknown-unknown`
/// component: a `dlmalloc` `#[global_allocator]` and a `#[panic_handler]` that
/// traps. This is the proven recipe that keeps the built component free of any
/// `wasi:*` imports.
///
/// Call this exactly once at the crate root of a plugin:
///
/// ```ignore
/// #![no_std]
/// extern crate alloc;
/// hellohq_plugin_sdk::setup_guest!();
/// ```
///
/// Plugins that need their own allocator/panic handler can skip this and supply
/// them directly; the only requirement is that the final component is `no_std`
/// and pulls in no `wasi` imports.
#[macro_export]
macro_rules! setup_guest {
    () => {
        #[global_allocator]
        static __HELLOHQ_ALLOC: $crate::__dlmalloc::GlobalDlmalloc =
            $crate::__dlmalloc::GlobalDlmalloc;

        #[panic_handler]
        fn __hellohq_panic(_info: &::core::panic::PanicInfo) -> ! {
            ::core::arch::wasm32::unreachable()
        }
    };
}

// Re-export dlmalloc so `setup_guest!` resolves without the author crate adding
// a direct dependency.
#[doc(hidden)]
pub use dlmalloc as __dlmalloc;
