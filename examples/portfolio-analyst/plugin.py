"""Portfolio AI Analyst — Tier-1 Python sidecar (compute half of a WebView plugin)
=================================================================================
Demonstrates ``ai:inference`` AND a Tier-1 sidecar driving a WebView UI.

This is the *compute* half. The *UI* half lives in ``ui/`` (a React + Vite
WebView bundle) and calls these functions through the host bridge — see this
example's README.

The plugin reads the user's aggregated portfolio data (names + aggregated
values) and runs a short, two-step reasoning loop against the host AI backend:
  1. extract the key observations from the portfolio snapshot, then
  2. turn those observations into plain-language recommendations.
Between the two steps it pushes an ``analysis-progress`` event so the UI can
show what it is doing while the (potentially slow) compute runs.

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``analyse`` -> ``{"observations","recommendations","model","tokens"}``

Each returns plain DATA; the WebView renders it.

Sidecar invocation protocol
---------------------------
The host always calls the sidecar's ``run`` function with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``:
- ``args["context"]`` holds the host's pre-fetched, permission-gated reads,
  keyed by permission id (e.g. ``"read:portfolio_names"``,
  ``"read:aggregated_values"``). Denied/absent reads are simply omitted, so we
  read them defensively and degrade gracefully.
- ``args["input"]`` is the UI's ``host.compute(fn, args)`` payload verbatim, so
  we route on ``args["input"]["function"]`` (NOT the top-level ``function``,
  which is always ``"run"``).

Permissions required: read:portfolio_names, read:aggregated_values, ai:inference

The AI-harness boundary
-----------------------
The aggregated values may be derived from sensitive account data. We summarise
the *opaque* context JSON into a compact prompt and instruct the model never to
echo raw identifiers. Any user-supplied free text goes only in a ``user``
message, never the system prompt.

See: https://hellohq.io/docs/plugins/ai-inference
"""

from __future__ import annotations

import json
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, emit_event, host, serve

_SYSTEM = (
    "You are a concise financial analyst. You explain a person's portfolio in "
    "plain English for a non-expert. Never repeat or relay raw account "
    "identifiers, account numbers, or internal IDs — refer to holdings only by "
    "their human-readable names or in aggregate. Be specific, balanced, and "
    "avoid jargon. Do not give regulated financial advice; frame everything as "
    "general observations the reader can discuss with a professional."
)


def dispatch(function: str, args: Any):
    # The host always calls "run"; the UI's intended function + args are nested
    # under args["input"] (see the protocol note in the module docstring).
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "run")
    fn_args = inner.get("args") or {}
    context = (args or {}).get("context") or {}

    if fn == "analyse":
        return _analyse(context, fn_args)
    raise UnsupportedFunction(fn)


def _analyse(context: dict, fn_args: dict) -> dict:
    """Two-step AI reasoning loop over the prefetched portfolio context.

    Step 1 distils key observations; step 2 turns them into recommendations.
    An ``analysis-progress`` event is emitted before each step so the UI can
    surface progress while the compute runs.
    """
    portfolio_context = _build_context(context)
    user_note = str(fn_args.get("note", "")).strip()

    try:
        # ── Step 1: key observations ──────────────────────────────────────────
        emit_event(
            "analysis-progress",
            {"step": "observations", "status": "running"},
        )
        obs_user = (
            "Here is a snapshot of my portfolio:\n\n"
            f"{portfolio_context}\n\n"
            "List the key observations: overall balance, any concentration, and "
            "anything notable. Use 3-5 short bullet points."
        )
        if user_note:
            obs_user += f"\n\nThe user also asks: {user_note}"

        obs_resp = host.ai_complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": obs_user},
            ],
            max_tokens=400,
        )
        observations = obs_resp.get("content", "").strip()
        emit_event(
            "analysis-progress",
            {"step": "observations", "status": "done"},
        )

        # ── Step 2: recommendations from the observations ─────────────────────
        emit_event(
            "analysis-progress",
            {"step": "recommendations", "status": "running"},
        )
        rec_resp = host.ai_complete(
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Based on these observations about my portfolio:\n\n"
                        f"{observations}\n\n"
                        "Give 2-4 plain-language suggestions the reader could "
                        "consider, each as a short bullet point. Note these are "
                        "general ideas, not personalised advice."
                    ),
                },
            ],
            max_tokens=400,
        )
        recommendations = rec_resp.get("content", "").strip()
        emit_event(
            "analysis-progress",
            {"step": "recommendations", "status": "done"},
        )

    except PluginError as exc:
        # Token-budget exhaustion (and similar host-side limits) surface here.
        msg = str(getattr(exc, "message", exc))
        if "budget" in msg.lower() or "token" in msg.lower():
            return {
                "observations": "",
                "recommendations": (
                    "The AI token budget for this period has been reached. "
                    "Try again later or increase the budget in settings."
                ),
                "model": "",
                "tokens": 0,
            }
        raise

    tokens = (
        obs_resp.get("input_tokens", 0)
        + obs_resp.get("output_tokens", 0)
        + rec_resp.get("input_tokens", 0)
        + rec_resp.get("output_tokens", 0)
    )

    return {
        "observations": observations,
        "recommendations": recommendations,
        "model": rec_resp.get("model", "") or obs_resp.get("model", ""),
        "tokens": tokens,
    }


def _build_context(context: dict) -> str:
    """Summarise the opaque, permission-gated reads into a compact prompt.

    ``context`` is keyed by permission id. Reads may be absent (denied) — we
    degrade gracefully. We only surface human-readable names and aggregated
    values, never raw account identifiers.
    """
    names = context.get("read:portfolio_names")
    values = context.get("read:aggregated_values")

    parts: list[str] = []

    if names:
        labels = _portfolio_labels(names)
        if labels:
            parts.append("Portfolios: " + ", ".join(labels))

    if values is not None:
        parts.append("Aggregated values: " + _compact_json(values))

    if not parts:
        return "(No portfolio data was shared with this plugin.)"
    return "\n".join(parts)


def _portfolio_labels(names: Any) -> list[str]:
    """Pull human-readable names out of the portfolio_names snapshot.

    The snapshot is opaque JSON; we only read shallow ``name``/``label`` fields
    and never raw ids.
    """
    labels: list[str] = []
    items = names if isinstance(names, list) else names.get("portfolios", names) if isinstance(names, dict) else []
    if not isinstance(items, list):
        return labels
    for item in items:
        if isinstance(item, dict):
            label = item.get("name") or item.get("label")
            if label:
                labels.append(str(label))
        elif isinstance(item, str):
            labels.append(item)
    return labels


def _compact_json(value: Any) -> str:
    """Render a value as compact JSON, truncated so prompts stay small."""
    try:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 1200 else text[:1200] + "…"


if __name__ == "__main__":
    serve(dispatch)
