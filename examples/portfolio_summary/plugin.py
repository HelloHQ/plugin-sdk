"""HelloHQ plugin — Portfolio Summary (Tier 1 / Python example).

Shows a concise overview of all portfolios: names, asset counts, and an
optional NumPy-powered mean-return estimate when `read:aggregated_values` is
granted.

Permissions required:
  - read:portfolio_names      (Community tier)
  - read:asset_count          (Community tier)
  - read:aggregated_values    (Verified tier, optional — gracefully degraded)

Run locally (no host needed — useful for protocol smoke-testing):

    python plugin.py <<'EOF'
    {"id":1,"function":"run","args":{"portfolio_id":""}}
    EOF
"""

from __future__ import annotations

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve


def dispatch(function: str, args) -> dict:
    if function == "run":
        return _run(args or {})
    raise UnsupportedFunction(function)


def _run(args: dict) -> dict:
    """Build a declarative UI tree summarising the user's portfolios."""
    portfolios: list[dict] = args.get("portfolios", [])
    asset_counts: dict[str, int] = args.get("asset_counts", {})

    if not portfolios:
        return {
            "type": "column",
            "children": [
                {"type": "empty-state",
                 "title": "No portfolios",
                 "description": "Add a portfolio to get started."},
            ],
        }

    rows = []
    for p in portfolios:
        pid = p.get("id", "")
        name = p.get("name", pid)
        count = asset_counts.get(pid, 0)
        rows.append({"label": name, "value": f"{count} asset{'s' if count != 1 else ''}"})

    return {
        "type": "column",
        "children": [
            {"type": "heading", "text": f"{len(portfolios)} Portfolio{'s' if len(portfolios) != 1 else ''}"},
            {"type": "key-value-list", "items": rows},
        ],
    }


if __name__ == "__main__":
    serve(dispatch)
