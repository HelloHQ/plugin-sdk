"""Property value estimates - Tier-1 Python sidecar (compute half).

INFORMATION ONLY: summarises recent comparable sales for an area and property type the
person chooses (Singapore HDB resale, England and Wales Price Paid Data, France DVF,
Ireland PPR from a CSV the person provides). It is not a valuation and not advice.

All logic lives in the ``property_estimates`` package (pure, tested without network). This
file is a thin adapter from the sidecar SDK to the package's narrow ``Host`` interface.
``build.sh`` bundles package + adapter into the single-file ``dist/plugin.py`` that ships.

Functions (``host.compute(fn, args)`` from a UI, or the sidecar ``input.function``):
- ``estimate``: args = {region, ...region fields..., include_proposal_preview?}
- ``propose``:  same args; builds the proposal and hands it to the host. PENDING HOST
  SUPPORT: until the host's propose API exists this returns the error code
  ``pending_host_support`` and sends nothing.

Permissions required: network:fetch (4 origins, see manifest.json). Pending host support:
propose:valuations, read:external_input.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, host, serve
from hellohq_plugin_sdk.protocol import ERR_EXECUTION_FAILED, ERR_INVALID_INPUT

from property_estimates.errors import ParseError, PluginCoreError, ValidationError
from property_estimates.hostapi import FetchRequest, FetchResponse, Receipt
from property_estimates.service import estimate_property, submit_proposal


class SidecarHost:
    """Adapts the SDK's existing host calls to the plugin's narrow Host interface."""

    def fetch(self, request: FetchRequest) -> FetchResponse:
        reply = host.fetch(
            request.url,
            method=request.method,
            headers=dict(request.headers),
            body=request.body or "",
        )
        body = reply.get("body", "")
        if not isinstance(body, str):
            # The SDK hands back bytes when the host sent the body base64 because it
            # was not UTF-8. Every source this plugin reads is JSON or CSV text, so
            # that is a bad response; str(bytes) would silently corrupt it.
            raise ParseError("response body is not UTF-8 text")
        return FetchResponse(
            status=int(reply.get("status", 0)),
            headers=reply.get("headers") or {},
            body=body,
        )

    def propose(self, proposals: Any) -> list[Receipt]:
        # No SDK call exists for this yet; do not invent one. See README "Blocked on host".
        raise NotImplementedError

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _guard(fn, *args):
    try:
        return fn(*args)
    except PluginCoreError as exc:
        code = ERR_INVALID_INPUT if isinstance(exc, ValidationError) else ERR_EXECUTION_FAILED
        raise PluginError(f"[{exc.code}] {exc.message}", code) from exc


def dispatch(function: str, args: Any):
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "estimate")
    fn_args = inner.get("args") or {}
    if fn == "estimate":
        return _guard(estimate_property, SidecarHost(), fn_args)
    if fn == "propose":
        sidecar_host = SidecarHost()
        result = _guard(estimate_property, sidecar_host, fn_args)
        return {"estimate": result, "receipts": _guard(submit_proposal, sidecar_host, result)}
    raise UnsupportedFunction(fn)


if __name__ == "__main__":
    serve(dispatch)
