# SPDX-License-Identifier: Apache-2.0
"""Hello Python: the minimal reference Tier-1 (Python sidecar) plugin.

On ``run`` it lists the workspace's portfolio names as a declarative UI
document, like the Wasm ``hello-world`` example. Standard library only; it
opens no file, socket or subprocess and makes no host call. ``bundle.py``
joins this file with the SDK's sidecar loop into the shipped ``plugin.py``.

Input contract (matched against the HelloHQ app, HelloHQ/hellohq):

* The host always calls the RPC function ``run`` with
  ``args = {"context": {...}, "input": {"function": "main", "args": {}}}``
  (lib/app/utils/service/plugin_sidecar_bridge.dart ``run``; the first input
  comes from plugin_runner_view.dart).
* A Tier-1 plugin has no portfolio host call: the host reads the names BEFORE
  the run and passes them as ``context["read:portfolio_names"]``, a list of
  ``{"id": ..., "name": ...}``. When the read is not granted (or refused) the
  key is simply absent (plugin_sidecar_bridge.dart).
* ``ui_type: "declarative"``: the RPC ``result`` must be ONE component object
  (``{"type": ...}``), a dict, not a JSON string; the app passes it to its
  declarative renderer (plugin_tier1_executor.dart ``_runSidecar`` ->
  plugin_runner_view.dart -> plugin_declarative_renderer.dart).
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk.protocol import UnsupportedFunction
from hellohq_plugin_sdk.sidecar import serve

#: The permission whose pre-fetched read the host passes in ``context``.
NAMES_PERMISSION = "read:portfolio_names"

#: The app's renderer truncates a table at this many rows
#: (``PluginUiLimits.maxTableRows``); cap explicitly and say so instead.
MAX_TABLE_ROWS = 500

HEADING = "Hello, Python!"


def portfolio_names_ui(names: list[str] | None) -> dict[str, Any]:
    """The declarative UI document for ``names``; ``None`` = the host refused the read.

    Pure: no I/O, no host call. Returns one ``column`` node whose children are
    a ``heading`` and then either the summary ``text`` + one-column ``table``,
    or an ``empty-state``.
    """
    children: list[dict[str, Any]] = [{"type": "heading", "level": 1, "text": HEADING}]
    if names is None:
        children.append(
            {
                "type": "empty-state",
                "title": "Portfolio names unavailable",
                "description": (
                    "Hello Python could not read your portfolio names. Check that the plugin "
                    'still has the "Read portfolio names" permission.'
                ),
            }
        )
    elif not names:
        children.append(
            {
                "type": "empty-state",
                "title": "No portfolios yet",
                "description": "Create a portfolio in HelloHQ and its name will be listed here.",
            }
        )
    else:
        n = len(names)
        noun = "portfolio" if n == 1 else "portfolios"
        children.append({"type": "text", "color": "muted", "content": f"You have {n} {noun} in this workspace."})
        children.append(
            {
                "type": "table",
                "columns": [{"key": "name", "label": "Portfolio"}],
                "rows": [{"name": name} for name in names[:MAX_TABLE_ROWS]],
            }
        )
        if n > MAX_TABLE_ROWS:
            children.append(
                {
                    "type": "text",
                    "color": "muted",
                    "size": "sm",
                    "content": f"Showing the first {MAX_TABLE_ROWS} of {n} portfolios.",
                }
            )
    return {"type": "column", "children": children}


def names_from_context(context: Any) -> list[str] | None:
    """The portfolio names in the host's pre-fetched ``context``, or ``None`` if absent.

    ``None`` means the host did not hand over the read (not granted, or
    refused); a malformed value is treated the same way rather than guessed at.
    """
    if not isinstance(context, dict):
        return None
    raw = context.get(NAMES_PERMISSION)
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str):
            names.append(name)
    return names


def dispatch(function: str, args: Any) -> dict[str, Any]:
    """The sidecar's RPC handler: ``run`` -> the declarative UI document."""
    if function != "run":
        raise UnsupportedFunction(str(function))
    args = args if isinstance(args, dict) else {}
    inner = args.get("input")
    fn = inner.get("function", "main") if isinstance(inner, dict) else "main"
    if fn not in ("main", "run"):
        raise UnsupportedFunction(str(fn))
    return portfolio_names_ui(names_from_context(args.get("context")))


if __name__ == "__main__":
    serve(dispatch)
