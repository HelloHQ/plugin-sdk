"""Property value estimates - Tier-1 Python sidecar (compute half).

INFORMATION ONLY: summarises recent comparable sales for an area and property type the
person chooses (Singapore HDB resale, England and Wales Price Paid Data, France DVF,
Ireland PPR from a CSV the person provides). It is not a valuation and not advice.

All logic lives in the ``property_estimates`` package (pure, tested without network). This
file is a thin adapter from the sidecar SDK to the package's narrow ``Host`` interface.
``build.sh`` bundles package + adapter into the single-file ``dist/plugin.py`` that ships.

Functions (``host.compute(fn, args)`` from a UI, or the sidecar ``input.function``):
- ``estimate``: args = {region, ...region fields..., include_proposal_preview?}
- ``propose``:  same args; builds the proposal and hands it to the host with
  ``hellohq_plugin_sdk.host.propose`` (SDK >= 0.2.0). Returns
  ``{"estimate", "proposal", "submission"}``: ``submission.status`` is ``submitted`` (one
  receipt per proposal: queued, duplicate, superseded_older, unchanged, suppressed or invalid +
  reason), ``pending_host_support`` (the host has no ``propose``: nothing was sent; needs a host
  with propose-only writes, hellohq with plugins enabled), or ``permission_denied`` / ``failed``
  (the host refused the call; ``code`` and ``retryable`` say why). The person approves each
  suggestion in the app; nothing is saved before that.

Permissions required: network:fetch (4 origins, see manifest.json), propose:valuations
(scope.kinds ["home"]), read:external_input (csv).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, host, serve
from hellohq_plugin_sdk.protocol import ERR_EXECUTION_FAILED, ERR_INVALID_INPUT

from property_estimates.errors import (
    ParseError,
    PendingHostSupport,
    PluginCoreError,
    ProposeRefused,
    ValidationError,
)
from property_estimates.hostapi import FetchRequest, FetchResponse, Receipt
from property_estimates.proposal import build_valuation_proposal, to_wire
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
        try:
            from hellohq_plugin_sdk.proposals import ProposeError, ProposeUnsupported
        except ImportError as exc:  # an SDK older than 0.2.0 has no propose
            raise PendingHostSupport(
                "the installed hellohq-plugin-sdk has no propose (needs >= 0.2.0); nothing was sent"
            ) from exc
        try:
            receipts = host.propose(list(proposals))
        except ProposeUnsupported as exc:
            raise PendingHostSupport(
                "the host does not support propose (it needs propose-only writes); nothing was sent"
            ) from exc
        except ProposeError as exc:
            raise ProposeRefused(
                exc.message, code=exc.code, reason=exc.reason, retryable=exc.retryable
            ) from exc
        return [Receipt(index=r.index, outcome=str(r.outcome), reason=r.reason) for r in receipts]

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
        submission = _guard(submit_proposal, sidecar_host, result)
        proposal = build_valuation_proposal(result, region=str(result.get("region", "")))
        return {"estimate": result, "proposal": to_wire(proposal), "submission": submission}
    raise UnsupportedFunction(fn)


if __name__ == "__main__":
    serve(dispatch)
