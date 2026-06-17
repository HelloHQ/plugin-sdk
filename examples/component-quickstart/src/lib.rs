// SPDX-License-Identifier: Apache-2.0
//
//! HelloHQ Tier-2 Component Model quickstart.
//!
//! A small headless plugin built with `hellohq-plugin-sdk` against the
//! canonical `hellohq:plugin@0.1.0` WIT. On `run` it:
//!
//!   1. logs a banner (`hq::log`),
//!   2. reads the workspace portfolio names (`hq::workspace`),
//!   3. stores + reads back a value (`hq::storage`),
//!   4. emits an event (`hq::events`),
//!   5. returns a compact ASCII summary `"<n-portfolios>|<roundtrip-ok>"`.
//!
//! It deliberately touches only `workspace` / `storage` / `events` / `log`, so
//! `wasm-tools component new` tree-shakes `inference` (and everything else) out
//! of the built component — see `build.sh` for the proof. Streaming inference
//! is a documented second step in `INFERENCE.md` (it needs an async `run`).

#![no_std]

extern crate alloc;

use alloc::format;
use alloc::string::String;
use alloc::vec::Vec;

use hellohq_plugin_sdk::{export_plugin, hq, Plugin, PluginMetadata};

// Wire the dlmalloc global allocator + a trapping panic handler so the built
// component imports only `hellohq:plugin/*` (no `wasi:*`).
hellohq_plugin_sdk::setup_guest!();

struct Quickstart;

impl Plugin for Quickstart {
    fn init() {
        hq::log::info("component-quickstart: init");
    }

    fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
        hq::log::debug("component-quickstart: run start");

        // 1. Read workspace portfolio names (permission-gated).
        let names = hq::workspace::read_portfolio_names().map_err(|e| e.message)?;
        hq::log::info(&format!("read {} portfolio name(s)", names.len()));

        // 2. Storage round-trip: set "greeting" -> read it back.
        hq::storage::set("greeting", b"hello").map_err(|e| e.message)?;
        let roundtrip = match hq::storage::get("greeting").map_err(|e| e.message)? {
            Some(bytes) => bytes == b"hello",
            None => false,
        };

        // 3. Emit an event (best-effort; ignore a cap/denial here).
        hq::events::emit("quickstart-ran", b"ok").ok();

        // 4. Compact summary the host can assert: "<n>|<ok>", e.g. "3|1".
        let summary = format!("{}|{}", names.len(), if roundtrip { 1 } else { 0 });
        hq::log::debug("component-quickstart: run done");
        Ok(summary.into_bytes())
    }

    fn metadata() -> PluginMetadata {
        PluginMetadata {
            id: String::from("component-quickstart"),
            version: String::from("0.1.0"),
        }
    }
}

export_plugin!(Quickstart);
