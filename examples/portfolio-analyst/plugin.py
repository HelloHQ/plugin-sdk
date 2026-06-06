"""Finance AI Agent example: Portfolio AI Analyst
==================================================
Tier 1 (Python sidecar) plugin — Smolagents tool-calling pattern.

The host calls ``dispatch("run", {"portfolio_names": [...], "asset_counts": [...]})``.
The plugin runs an agentic loop: the AI decides which portfolios to examine
by calling tools, then produces a final risk/concentration report.

Permissions required: read:portfolio_names, read:asset_count, ai:inference (v2)

Pattern demonstrated
--------------------
This follows the **Smolagents tool-calling pattern** (hand-rolled, no dependency):
  1. Define tools as JSON schema objects (same format as Claude tool_use).
  2. Run an agent loop: call AI → handle tool → append result → repeat.
  3. The AI calls ``done(report=…)`` to end the loop.
  4. Render the report as a declarative UI.

For full Smolagents integration (CodeAgent, HfApiModel custom transport, etc.) see:
  https://hellohq.io/docs/plugins/ai-agents#smolagents
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import PluginError, host, serve

# ── Tool schema (Anthropic tool_use format) ───────────────────────────────────

_TOOLS = [
    {
        "name": "get_portfolio_summary",
        "description": (
            "Return the asset and debt item counts for a single portfolio. "
            "Call this for each portfolio you want to analyse."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "portfolio_id": {"type": "string", "description": "The portfolio ID to look up."}
            },
            "required": ["portfolio_id"],
        },
    },
    {
        "name": "done",
        "description": "Finish analysis. Call this with the final written report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "report": {
                    "type": "string",
                    "description": "The final 3-5 sentence analysis report.",
                }
            },
            "required": ["report"],
        },
    },
]

_SYSTEM = (
    "You are a portfolio risk analyst. You have access to tools that return "
    "portfolio item counts. Use get_portfolio_summary for each portfolio, then "
    "call done() with a 3-5 sentence report identifying concentration risk, "
    "asset/debt imbalance, and any rebalancing suggestions. "
    "Be concise and specific."
)


# ── Agentic loop ──────────────────────────────────────────────────────────────

def _run_agent(
    portfolios: list[dict],
    counts_by_id: dict[str, dict],
) -> str:
    """Run the tool-calling loop until the AI calls done()."""
    names_list = ", ".join(
        f"'{p['name']}' (id={p['id']!r})" for p in portfolios
    )
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Analyse these portfolios: {names_list}. "
                "Use the tools to get the data you need, then call done()."
            ),
        }
    ]

    for _step in range(10):  # safety cap: 10 tool calls max
        resp = host.ai_complete(messages, tools=_TOOLS, max_tokens=512)

        tool_use = resp.get("tool_use")
        if not tool_use:
            # Model produced text without a tool call — treat as final answer
            return resp.get("content", "No report produced.")

        tool_name = tool_use.get("name", "")
        tool_input = tool_use.get("input", {})

        if tool_name == "done":
            return tool_input.get("report", "")

        if tool_name == "get_portfolio_summary":
            pid = tool_input.get("portfolio_id", "")
            entry = counts_by_id.get(pid)
            if entry:
                result_text = (
                    f"Portfolio '{entry.get('name', pid)}': "
                    f"{entry.get('asset_items', 0)} asset items, "
                    f"{entry.get('debt_items', 0)} debt items "
                    f"(total {entry.get('total_items', 0)})"
                )
            else:
                result_text = f"Portfolio id={pid!r} not found."
        else:
            result_text = f"Unknown tool: {tool_name}"

        # Append the assistant turn and the tool result
        messages.append({
            "role": "assistant",
            "content": resp.get("content", ""),
            "tool_use": tool_use,
        })
        messages.append({
            "role": "user",
            "content": f"[Tool result for {tool_name}]: {result_text}",
        })

    return "Analysis reached the step limit."


# ── Plugin dispatch ───────────────────────────────────────────────────────────

def dispatch(function: str, args: Any) -> Any:
    if function not in ("", "run"):
        raise PluginError(f"unsupported function: {function}")

    portfolios: list[dict] = (args or {}).get("portfolio_names", [])
    counts_list: list[dict] = (args or {}).get("asset_counts", [])

    if not portfolios:
        return {
            "type": "empty-state",
            "title": "No portfolios",
            "description": "Grant read:portfolio_names in the manifest.",
        }

    # Enrich counts with portfolio name for tool results
    name_by_id = {p["id"]: p["name"] for p in portfolios}
    counts_by_id = {
        c["id"]: {**c, "name": name_by_id.get(c["id"], c["id"])}
        for c in counts_list
    }

    try:
        report = _run_agent(portfolios, counts_by_id)
    except PluginError as exc:
        report = f"AI analysis unavailable: {exc}"

    # Summary table: one row per portfolio
    kv_items = [
        {
            "label": p["name"],
            "value": (
                f"{counts_by_id[p['id']]['total_items']} items"
                if p["id"] in counts_by_id
                else "—"
            ),
        }
        for p in portfolios
    ]

    return {
        "type": "column",
        "children": [
            {"type": "heading", "text": "Portfolio AI Analyst"},
            {"type": "key-value-list", "items": kv_items},
            {"type": "divider"},
            {
                "type": "section",
                "title": "AI Analysis",
                "children": [{"type": "text", "text": report}],
            },
        ],
    }


if __name__ == "__main__":
    serve(dispatch)
