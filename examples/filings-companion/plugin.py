"""Holdings filings companion - Tier-1 Python sidecar (compute half).

INFORMATION ONLY: for tickers, ISINs, CUSIPs, FIGIs or CIKs the person supplies, shows recent
SEC filings and the latest annual XBRL facts as the company reported them (SEC EDGAR,
data.sec.gov), using OpenFIGI (keyless) to map ISIN/CUSIP/FIGI to a US ticker. It reads
public data only and writes nothing to the person's data. It is not advice.

All logic lives in the ``filings_companion`` package (pure, tested without network). This file
is a thin adapter from the sidecar SDK to the package's narrow ``Host`` interface.
``build.sh`` bundles package + adapter into the single-file ``dist/plugin.py`` that ships.

Functions (``host.compute(fn, args)`` from a UI, or the sidecar ``input.function``):
- ``configure_contact``: args = {contact: "Your Name you@yourdomain.com"}. Stored in plugin
  storage and used in the SEC-required User-Agent. SEC requests are refused until set.
- ``contact_status``: whether a valid contact is configured.
- ``lookup``: args = {identifiers: [{type, value}, ...up to 10], forms?, filings_limit?,
  include_facts?}.

Permissions required: network:fetch (3 origins, see manifest.json), plugin:storage.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, host, serve
from hellohq_plugin_sdk.protocol import ERR_EXECUTION_FAILED, ERR_INVALID_INPUT

from filings_companion.errors import (
    ContactNotConfigured,
    ParseError,
    PluginCoreError,
    ValidationError,
)
from filings_companion.hostapi import FetchRequest, FetchResponse
from filings_companion.service import configure_contact, contact_status, lookup_holdings


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

    def storage_get(self, key: str) -> str | None:
        return host.storage_get(key)

    def storage_set(self, key: str, value: str) -> None:
        host.storage_set(key, value)

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _guard(fn, *args):
    try:
        return fn(*args)
    except PluginCoreError as exc:
        invalid = isinstance(exc, ValidationError | ContactNotConfigured)
        raise PluginError(
            f"[{exc.code}] {exc.message}", ERR_INVALID_INPUT if invalid else ERR_EXECUTION_FAILED
        ) from exc


def dispatch(function: str, args: Any):
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "lookup")
    fn_args = inner.get("args") or {}
    if fn == "configure_contact":
        return _guard(configure_contact, SidecarHost(), fn_args)
    if fn == "contact_status":
        return contact_status(SidecarHost())
    if fn == "lookup":
        return _guard(lookup_holdings, SidecarHost(), fn_args)
    raise UnsupportedFunction(fn)


if __name__ == "__main__":
    serve(dispatch)
