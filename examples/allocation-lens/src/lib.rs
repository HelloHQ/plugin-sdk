//! Finance AI Agent example: Asset Allocation Narrative
//!
//! Reads portfolio names and asset counts, then calls `host::ai_complete` to
//! generate a plain-language allocation summary. The AI never sees raw values —
//! only item counts and structural shape.
//!
//! Required permissions:
//!   read:portfolio_names, read:asset_count, ai:inference (v2)
//!
//! Build:
//!   cargo build --target wasm32-unknown-unknown --release

use hellohq_plugin_sdk::{host, plugin, ui, AiMessage, InferenceOpts, PluginError, View};
use serde_json::Value;

plugin! {
    fn run(input: &[u8]) -> Result<Vec<u8>, PluginError> {
        let args: Value = serde_json::from_slice(input).unwrap_or(Value::Null);
        let portfolio_id = args["args"]["portfolio_id"].as_str();

        // 1. Read portfolio names (for display labels)
        let names = host::read_portfolio_names().unwrap_or_default();

        // 2. Read allocation counts for the requested portfolio (or all)
        let counts = host::read_asset_count(portfolio_id)
            .map_err(|e| PluginError::ExecutionFailed(e.to_string()))?;

        let portfolios = counts["portfolios"]
            .as_array()
            .cloned()
            .unwrap_or_default();

        if portfolios.is_empty() {
            return Ok(ui::empty_state(
                "No allocation data",
                Some("Grant read:asset_count in the plugin manifest."),
            )
            .to_bytes());
        }

        // 3. Build a context string for the AI prompt (no financial values)
        let ctx = build_context(&names, &portfolios);

        // 4. Ask the host AI backend for a plain-language insight
        let ai = host::ai_complete(
            vec![AiMessage::user(&format!(
                "You are a concise financial advisor. In 2-3 sentences describe what \
                 the following asset/debt allocation structure implies about the \
                 investor's position and flag any concentration concern. \
                 Do not invent numbers not provided.\n\n{ctx}"
            ))],
            InferenceOpts { max_tokens: 200, temperature: Some(0.3) },
        )
        .map_err(|e| PluginError::ExecutionFailed(e.to_string()))?;

        // 5. Build the declarative UI
        Ok(ui::column(vec![
            ui::heading("Asset Allocation"),
            allocation_table(&names, &portfolios),
            ui::divider(),
            ui::section("AI Insight", vec![ui::text(&ai.content)]),
        ])
        .to_bytes())
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn build_context(
    names: &[hellohq_plugin_sdk::PortfolioName],
    portfolios: &[Value],
) -> String {
    portfolios
        .iter()
        .map(|p| {
            let id = p["id"].as_str().unwrap_or("?");
            let label = names
                .iter()
                .find(|n| n.id == id)
                .map(|n| n.name.as_str())
                .unwrap_or(id);
            let assets = p["asset_items"].as_u64().unwrap_or(0);
            let debts = p["debt_items"].as_u64().unwrap_or(0);
            let total = assets + debts;
            let ratio = if total > 0 {
                format!("{:.0}%", 100.0 * assets as f64 / total as f64)
            } else {
                "n/a".into()
            };
            format!(
                "- {label}: {assets} asset item(s), {debts} debt item(s), \
                 {total} total ({ratio} assets)"
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn allocation_table(
    names: &[hellohq_plugin_sdk::PortfolioName],
    portfolios: &[Value],
) -> View {
    let rows: Vec<Vec<String>> = portfolios
        .iter()
        .map(|p| {
            let id = p["id"].as_str().unwrap_or("?");
            let label = names
                .iter()
                .find(|n| n.id == id)
                .map(|n| n.name.clone())
                .unwrap_or_else(|| id.to_string());
            let assets = p["asset_items"].as_u64().unwrap_or(0);
            let debts = p["debt_items"].as_u64().unwrap_or(0);
            let total = assets + debts;
            let ratio = if total > 0 {
                format!("{:.0}%", 100.0 * assets as f64 / total as f64)
            } else {
                "—".into()
            };
            vec![label, assets.to_string(), debts.to_string(), ratio]
        })
        .collect();

    ui::table(vec!["Portfolio", "Assets", "Debts", "Asset Ratio"], rows)
}
