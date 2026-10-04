"""Macro context - Tier-1 Python sidecar entry point (READ-ONLY).

Thin adapter only: all logic lives in the ``macro_context`` package (pure,
unit-tested, no SDK imports). ``build.sh`` bundles it into the single-file
sidecar artifact ``dist/plugin.py``.

Functions (host calls ``run`` with
``args = {"context": {...}, "input": {"function": <fn>, "args": {...}}}``):

* ``context`` (default) - optional ``args``: ``sections`` (subset of ecb,
  worldbank, treasury, sgfx), ``ecb_series``, ``worldbank_countries``,
  ``worldbank_indicators``, ``treasury_months``, ``fx_currencies``,
  ``fx_months``. Returns series with as-of dates, units and provenance.

Permissions: network:fetch only (data-api.ecb.europa.eu, api.worldbank.org,
api.fiscaldata.treasury.gov, data.gov.sg). No propose/write permission.
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve
from hellohq_plugin_sdk.protocol import ERR_INVALID_INPUT

from macro_context.context import fetch_context, parse_request
from macro_context.errors import ValidationError
from macro_context.sdk_host import SdkHost


def dispatch(function: str, args: Any):
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "context")
    if fn not in ("context", "run"):
        raise UnsupportedFunction(str(fn))
    try:
        request = parse_request(inner.get("args"))
    except ValidationError as exc:
        raise PluginError(exc.message, ERR_INVALID_INPUT) from exc
    return fetch_context(SdkHost(), request)


if __name__ == "__main__":
    serve(dispatch)
