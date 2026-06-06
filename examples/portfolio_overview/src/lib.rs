//! Reference HelloHQ Tier-2 plugin, authored with `hellohq-plugin-sdk`.
//!
//! Compare with the hand-written raw-ABI version in the host repo
//! (`hellohq/examples/plugins/portfolio_overview`): the SDK removes every
//! pointer, the manual `hq_read` framing, and the JSON string-building.
//!
//! Build:
//! ```bash
//! cargo build --target wasm32-unknown-unknown --release
//! ```

use hellohq_plugin_sdk::{host, plugin, ui, PluginError};

plugin! {
    fn run(_input: &[u8]) -> Result<Vec<u8>, PluginError> {
        // Permission-gated read. If `read:portfolio_names` wasn't granted the
        // host returns a denial; degrade gracefully to an empty state.
        let portfolios = host::read_portfolio_names().unwrap_or_default();

        if portfolios.is_empty() {
            return Ok(ui::empty_state(
                "No portfolios",
                Some("This plugin needs the read:portfolio_names permission."),
            )
            .to_bytes());
        }

        let items = portfolios
            .iter()
            .enumerate()
            .map(|(i, p)| (format!("Portfolio {}", i + 1), p.name.clone()))
            .collect();

        Ok(ui::column(vec![
            ui::heading(&format!("Portfolios ({})", portfolios.len())),
            ui::key_value_list(items),
        ])
        .to_bytes())
    }
}
