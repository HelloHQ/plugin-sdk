"""FX Opportunity Advisor — Tier-1 Python sidecar (compute half of a WebView plugin)
==================================================================================
Demonstrates ``read:currency_rates`` + ``ai:inference`` AND a Tier-1 sidecar
driving a WebView UI.

This is the *compute* half. The *UI* half lives in ``ui/`` (a Vue 3 + Vite
WebView bundle) and calls these functions through the host bridge — see this
example's README.

The plugin reads the workspace's current currency rates (prefetched, gated by
``read:currency_rates``), summarises them into a compact prompt, and asks the
host AI backend for a STRICT-JSON FX opportunity analysis: which currencies look
strong vs weak against USD, plus a short portfolio recommendation.

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``analyse`` -> ``{"headline","strong","weak","recommendation","model","tokens"}``

Each returns plain DATA; the WebView renders it.

Sidecar invocation protocol
---------------------------
The host always calls the sidecar's ``run`` function with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``:
- ``args["context"]`` holds the host's pre-fetched, permission-gated reads,
  keyed by permission id (e.g. ``"read:currency_rates"``). Denied/absent reads
  are simply omitted, so we read them defensively and degrade gracefully.
- ``args["input"]`` is the UI's ``host.compute(fn, args)`` payload verbatim, so
  we route on ``args["input"]["function"]`` (NOT the top-level ``function``,
  which is always ``"run"``).

Permissions required: read:currency_rates, ai:inference

The AI-harness boundary
-----------------------
The currency rates are opaque JSON. We summarise them into a compact prompt and
instruct the model to respond as strict JSON, and never to echo raw internal
identifiers. The model returns currency *ids* (e.g. ``"eur"``) in the
``strong``/``weak`` lists, which we treat as opaque labels for the UI. There is
no user free text in this example; any user-supplied text would go only in a
``user`` message, never the system prompt.

Structured output: we instruct the model to emit JSON and parse it with
``json.loads``. On parse failure we DEGRADE gracefully to a result carrying the
raw text as the recommendation — no pydantic dependency (it may be absent in the
sandbox).

See: https://hellohq.io/docs/plugins/ai-inference
"""

from __future__ import annotations

import json
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, emit_event, host, serve

_SYSTEM = (
    "You are a concise FX (foreign-exchange) analyst. Given current exchange "
    "rates relative to USD, identify which currencies look relatively strong "
    "and which look relatively weak against USD, and give a short, balanced "
    "portfolio implication. Never repeat raw internal identifiers; refer to "
    "currencies by their short ids only in the strong/weak lists. Do not give "
    "regulated financial advice; frame everything as general observations the "
    "reader can discuss with a professional. "
    "Respond ONLY with valid JSON (no markdown, no prose, no code fences) "
    'matching this exact shape: {"headline": "<one sentence>", '
    '"strong": ["<id>", ...], "weak": ["<id>", ...], '
    '"recommendation": "<2 sentences about portfolio implications>"}'
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
    """Summarise the prefetched currency rates and ask the AI for a structured
    FX opportunity analysis.

    Returns ``{"headline","strong","weak","recommendation","model","tokens"}``.
    On JSON-parse failure the raw model text degrades into ``recommendation``.
    """
    rates_text = _build_rates(context)

    emit_event("analysis-progress", {"step": "analyse", "status": "running"})
    try:
        resp = host.ai_complete(
            [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Here are the current exchange rates relative to USD:\n\n"
                        f"{rates_text}\n\n"
                        "Give your FX opportunity analysis as the JSON described."
                    ),
                },
            ],
            max_tokens=400,
            temperature=0.2,
        )
    except PluginError as exc:
        # Token-budget exhaustion (and similar host-side limits) surface here.
        msg = str(getattr(exc, "message", exc))
        if "budget" in msg.lower() or "token" in msg.lower():
            return {
                "headline": "AI token budget reached",
                "strong": [],
                "weak": [],
                "recommendation": (
                    "The AI token budget for this period has been reached. "
                    "Try again later or increase the budget in settings."
                ),
                "model": "",
                "tokens": 0,
            }
        raise
    finally:
        emit_event("analysis-progress", {"step": "analyse", "status": "done"})

    content = (resp.get("content") or "").strip()
    parsed = _parse_analysis(content)
    tokens = resp.get("input_tokens", 0) + resp.get("output_tokens", 0)

    return {
        **parsed,
        "model": resp.get("model", ""),
        "tokens": tokens,
    }


def _parse_analysis(content: str) -> dict:
    """Parse the model's JSON content into the analysis shape.

    Degrades gracefully: on any parse failure we keep the raw text as the
    recommendation and leave the structured fields empty.
    """
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (ValueError, TypeError):
        return {
            "headline": "FX analysis (unstructured)",
            "strong": [],
            "weak": [],
            "recommendation": content,
        }

    return {
        "headline": str(data.get("headline", "")),
        "strong": _str_list(data.get("strong")),
        "weak": _str_list(data.get("weak")),
        "recommendation": str(data.get("recommendation", "")),
    }


def _str_list(value: Any) -> list[str]:
    """Coerce an opaque value into a list of short string ids."""
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, int, float))]


def _build_rates(context: dict) -> str:
    """Summarise the opaque, permission-gated currency rates into a compact
    prompt. The read may be absent (denied) — we degrade gracefully.
    """
    rates = context.get("read:currency_rates")
    items = _rate_items(rates)

    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if cid == "usd":
            continue  # USD is the base; nothing to report against itself.
        symbol = item.get("symbol") or item.get("name") or cid or "?"
        name = item.get("name") or symbol
        rate = item.get("rate")
        if isinstance(rate, (int, float)):
            lines.append(f"  {cid} — {name} ({symbol}): 1 USD = {rate:.4f} {symbol}")
        elif cid:
            lines.append(f"  {cid} — {name} ({symbol})")

    if not lines:
        return "(No currency rate data was shared with this plugin.)"
    return "\n".join(lines)


def _rate_items(rates: Any) -> list:
    """Pull the list of rate records out of the opaque context value."""
    if isinstance(rates, list):
        return rates
    if isinstance(rates, dict):
        inner = rates.get("rates") or rates.get("currencies") or rates.get("currency_rates")
        if isinstance(inner, list):
            return inner
    return []


if __name__ == "__main__":
    serve(dispatch)
