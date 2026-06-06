"""Finance AI Agent example: FX Opportunity Advisor
==========================================================
Tier 1 (Python sidecar) plugin — PydanticAI structured-output pattern.

The host calls ``dispatch("run", {"currency_rates": [...]})`` with pre-fetched
currency rates (injected because read:currency_rates is granted). The plugin
asks the host AI backend for a structured FX analysis and renders it as a
declarative UI.

Permissions required: read:currency_rates, ai:inference (v2)

Pattern demonstrated
--------------------
This follows the **PydanticAI structured-output pattern**:
  1. Build a prompt that instructs the model to respond in a specific JSON shape.
  2. Parse and validate the response with Pydantic.
  3. Degrade gracefully to plain-text if parsing fails.

For full PydanticAI integration (custom Model class, agent runner, etc.) see:
  https://hellohq.io/docs/plugins/ai-agents#pydantic-ai
"""

from __future__ import annotations

import json
from typing import Any

from hellohq_plugin_sdk import PluginError, host, serve
from pydantic import BaseModel, ValidationError


# ── Structured output schema ──────────────────────────────────────────────────

class FxAnalysis(BaseModel):
    """Validated AI response for FX analysis."""

    headline: str
    strong: list[str]   # currency ids the model considers strong vs USD
    weak: list[str]     # currency ids the model considers weak vs USD
    recommendation: str


# ── Plugin dispatch ───────────────────────────────────────────────────────────

def dispatch(function: str, args: Any) -> Any:
    if function not in ("", "run"):
        raise PluginError(f"unsupported function: {function}")

    rates: list[dict] = args.get("currency_rates", []) if args else []
    if not rates:
        return {
            "type": "empty-state",
            "title": "No currency data",
            "description": "Grant read:currency_rates in the manifest.",
        }

    # Format rates as context for the AI (exclude the base currency)
    rates_text = "\n".join(
        f"  {r['symbol']} ({r['name']}): 1 USD = {r['rate']:.4f} {r['symbol']}"
        for r in rates
        if r.get("id") != "usd"
    )
    if not rates_text:
        return {"type": "empty-state", "title": "Only USD in workspace",
                "description": "Add non-USD currencies to see FX analysis."}

    prompt = (
        "You are an FX analyst. Given these current exchange rates relative to USD:\n"
        f"{rates_text}\n\n"
        "Respond ONLY with valid JSON (no markdown, no explanation) matching this exact shape:\n"
        '{"headline":"<one sentence>","strong":["<id>",…],"weak":["<id>",…],'
        '"recommendation":"<2 sentences about portfolio implications>"}'
    )

    try:
        resp = host.ai_complete(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.2,
        )
        analysis = FxAnalysis.model_validate_json(resp["content"])
    except (ValidationError, json.JSONDecodeError, KeyError, PluginError) as exc:
        # Degrade gracefully: show raw AI text if structured parsing fails
        raw = resp.get("content", str(exc)) if "resp" in dir() else str(exc)
        return _rates_table_only(rates, note=f"(Structured analysis unavailable: {exc})", raw_ai=raw)

    return _full_ui(rates, analysis)


# ── UI builders ───────────────────────────────────────────────────────────────

def _rates_table_only(rates: list[dict], *, note: str = "", raw_ai: str = "") -> dict:
    rows = [[r["symbol"], r["name"], f"{r['rate']:.4f}"] for r in rates]
    children: list[dict] = [
        {"type": "heading", "text": "FX Rates"},
        {
            "type": "table",
            "columns": [{"label": "Symbol"}, {"label": "Currency"}, {"label": "Rate (USD)"}],
            "rows": rows,
        },
    ]
    if note:
        children.append({"type": "text", "text": note})
    if raw_ai:
        children.append({"type": "section", "title": "AI Response", "children": [
            {"type": "text", "text": raw_ai}
        ]})
    return {"type": "column", "children": children}


def _full_ui(rates: list[dict], analysis: FxAnalysis) -> dict:
    rate_rows = [[r["symbol"], r["name"], f"{r['rate']:.4f}"] for r in rates]

    # Build signal badges: green = strong, red = weak
    strong_ids = set(analysis.strong)
    weak_ids = set(analysis.weak)
    badges = (
        [{"label": c.upper(), "color": "green"} for c in analysis.strong]
        + [{"label": c.upper(), "color": "red"} for c in analysis.weak]
        + [
            {"label": r["symbol"], "color": "neutral"}
            for r in rates
            if r.get("id") not in strong_ids and r.get("id") not in weak_ids
            and r.get("id") != "usd"
        ]
    )

    return {
        "type": "column",
        "children": [
            {"type": "heading", "text": "FX Opportunity Advisor"},
            {"type": "text", "text": analysis.headline},
            {
                "type": "table",
                "columns": [{"label": "Symbol"}, {"label": "Currency"}, {"label": "Rate (USD)"}],
                "rows": rate_rows,
            },
            {"type": "divider"},
            {
                "type": "section",
                "title": "Signal",
                "children": [{"type": "badge-row", "badges": badges}],
            },
            {
                "type": "section",
                "title": "Portfolio Implication",
                "children": [{"type": "text", "text": analysis.recommendation}],
            },
        ],
    }


if __name__ == "__main__":
    serve(dispatch)
