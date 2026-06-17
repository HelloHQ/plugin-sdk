"""Portfolio Summary — Tier-1 Python sidecar (compute half of a WebView plugin)
==============================================================================
Demonstrates a Tier-1 sidecar that summarises the workspace's portfolios from
the host's pre-fetched, permission-gated context — no live storage, no AI, just
plain-data shaping.

This is the *compute* half. The *UI* half lives in ``ui/`` (a framework-agnostic
WebView bundle, here vanilla TypeScript) and calls these functions through the
host bridge — see this example's README.

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``summary`` (and default) -> ``{"portfolios": [{"name", ...}], "count": N}``

Each returns plain DATA; the WebView renders it. No raw account identifiers
beyond portfolio names ever leave the sidecar.

Sidecar invocation protocol
---------------------------
The host always calls the sidecar's ``run`` function with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``:
- ``args["context"]`` holds the host's pre-fetched, permission-gated reads,
  keyed by permission id (e.g. ``"read:portfolio_names"``,
  ``"read:aggregated_values"``). Denied reads are omitted — read defensively and
  degrade gracefully if absent. The payloads are opaque JSON.
- ``args["input"]`` is the UI's ``host.compute(fn, args)`` payload verbatim, so
  we route on ``args["input"]["function"]``.

Permissions required:
  - read:portfolio_names      (portfolio names)
  - read:aggregated_values    (optional totals — gracefully degraded)

See: https://hellohq.io/docs/plugins
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import UnsupportedFunction, serve


def dispatch(function: str, args: Any):
    # The host always calls "run"; the UI's intended function + args are nested
    # under args["input"] (see the protocol note in the module docstring).
    ctx = (args or {}).get("context") or {}
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "summary")

    if fn in ("summary", "run"):
        return _summary(ctx)
    raise UnsupportedFunction(fn)


def _summary(ctx: dict) -> dict:
    # Pre-fetched, permission-gated reads. Both may be absent if denied.
    names = ctx.get("read:portfolio_names") or []
    aggregates = ctx.get("read:aggregated_values") or {}

    # `aggregates` is opaque JSON: tolerate either a {id|name: value} map or a
    # list of {id|name, total} records. Build a lookup keyed by id and name.
    totals: dict[str, Any] = {}
    if isinstance(aggregates, dict):
        totals = dict(aggregates)
    elif isinstance(aggregates, list):
        for row in aggregates:
            if not isinstance(row, dict):
                continue
            value = row.get("total", row.get("value"))
            for key in (row.get("id"), row.get("name")):
                if key is not None:
                    totals[str(key)] = value

    portfolios: list[dict] = []
    for entry in names:
        if isinstance(entry, dict):
            pid = entry.get("id")
            name = entry.get("name", pid)
        else:
            pid = None
            name = entry
        item: dict[str, Any] = {"name": str(name) if name is not None else ""}
        total = None
        if pid is not None and str(pid) in totals:
            total = totals[str(pid)]
        elif item["name"] in totals:
            total = totals[item["name"]]
        if total is not None:
            item["total"] = total
        portfolios.append(item)

    return {"portfolios": portfolios, "count": len(portfolios)}


if __name__ == "__main__":
    serve(dispatch)
