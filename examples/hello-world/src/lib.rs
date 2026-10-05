// SPDX-License-Identifier: Apache-2.0
//
//! Hello World (`com.hellohq.hello-world`) — the minimal HelloHQ reference
//! plugin listed in the public registry.
//!
//! A Tier-2 `hellohq:plugin@0.1.0` component built with `hellohq-plugin-sdk`.
//! On `run` it reads the workspace portfolio names (`hq::workspace`, gated by
//! `read:portfolio_names` — the only permission its manifest declares) and
//! returns a declarative UI document listing them, or a friendly empty state
//! when there are none (see [`ui`] for the exact output contract).
//!
//! It calls nothing but `workspace.read-portfolio-names` and `log`, so
//! `wasm-tools component new` tree-shakes every other interface (storage,
//! events, inference) out of the built component; build.sh prints the WIT
//! and CI asserts the import set.
//!
//! The crate is `no_std` on wasm32 only; on the host it is a normal `std`
//! crate so `cargo test` can unit-test the pure UI builder in [`ui`].

#![cfg_attr(target_arch = "wasm32", no_std)]

extern crate alloc;

pub mod ui;

#[cfg(target_arch = "wasm32")]
mod component {
    use alloc::string::String;
    use alloc::vec::Vec;

    use hellohq_plugin_sdk::{export_plugin, hq, Plugin, PluginMetadata};

    use crate::ui;

    // dlmalloc global allocator + trapping panic handler, so the built
    // component imports only `hellohq:plugin/*` (no `wasi:*`).
    hellohq_plugin_sdk::setup_guest!();

    struct HelloWorld;

    impl Plugin for HelloWorld {
        fn init() {
            hq::log::info("hello-world: init");
        }

        // The input (`{"function":…,"args":…}`) is ignored: Hello World has a
        // single screen and no actions.
        fn run(_input: Vec<u8>) -> Result<Vec<u8>, String> {
            let doc = match hq::workspace::read_portfolio_names() {
                Ok(portfolios) => {
                    let names: Vec<&str> = portfolios.iter().map(|p| p.name.as_str()).collect();
                    ui::portfolio_list(&names)
                }
                Err(e) => {
                    // Degrade to an in-pane message instead of failing the run.
                    hq::log::warn(&e.message);
                    ui::names_unavailable()
                }
            };
            Ok(doc.into_bytes())
        }

        fn metadata() -> PluginMetadata {
            PluginMetadata {
                id: String::from("com.hellohq.hello-world"),
                version: String::from(env!("CARGO_PKG_VERSION")),
            }
        }
    }

    export_plugin!(HelloWorld);
}
