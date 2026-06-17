// SPDX-License-Identifier: Apache-2.0
//
//! Streaming-inference quickstart.
//!
//! Built against the SDK's `inference-quickstart` world (`../../sdks/rust/wit`),
//! whose `run` is an `async func` — required because draining
//! `inference.complete`'s `stream<string>` yields. `run` sends one user message,
//! drains the token-delta stream concatenating each delta, logs the length, and
//! returns the completion text as bytes.
//!
//! It imports only `hellohq:plugin/{types, log, inference}`; `wasm-tools
//! component new` keeps it free of any `wasi:*` import.

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
    path: "../../../sdks/rust/wit",
    world: "inference-quickstart",
    generate_all,
});

use hellohq::plugin::inference::{complete, ChatMessage, InferenceOpts};
use hellohq::plugin::log::{write as log_write, Level};

struct Component;

impl Guest for Component {
    // Async because we drain the returned `stream<string>`.
    async fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
        log_write(Level::Info, "inference-quickstart: run");

        let messages = vec![ChatMessage {
            role: String::from("user"),
            content: String::from("Say hello in three words."),
        }];
        let opts = InferenceOpts { max_tokens: 64, temperature: None };

        let stream = match complete(&messages, opts) {
            Ok(s) => s,
            Err(e) => return Err(e.message),
        };

        // Drain token deltas, concatenating.
        let deltas: Vec<String> = stream.collect().await;
        let mut text = String::new();
        for d in deltas {
            text.push_str(&d);
        }

        log_write(Level::Info, "inference-quickstart: stream drained");
        Ok(text.into_bytes())
    }
}

export!(Component);
