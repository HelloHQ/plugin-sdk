"""Production ``Host`` adapter over ``hellohq_plugin_sdk`` (Tier-1 sidecar)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from wallet_tracker.errors import FetchError, HostUnsupported
from wallet_tracker.host import HttpResponse


class SdkHost:
    """Maps the narrow ``Host`` onto the Python SDK.

    ``fetch`` is real (``network:fetch``, Verified tier, Tier 1). ``propose``
    is NOT available: the SDK and the host protocol have no propose call yet,
    and this plugin must not invent one. It raises ``HostUnsupported`` and the
    caller returns the proposals as data instead.
    """

    def fetch(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: str = "",
    ) -> HttpResponse:
        from hellohq_plugin_sdk import PluginError
        from hellohq_plugin_sdk import host as sdk

        try:
            raw = sdk.fetch(url, method=method, headers=dict(headers or {}), body=body)
        except PluginError as exc:
            raise FetchError(str(exc), code=getattr(exc, "code", "fetch_error")) from exc
        text = raw.get("body") or ""
        if not isinstance(text, str):
            # The SDK hands back bytes when the host sent the body base64 because it
            # was not UTF-8. Every source this plugin reads is JSON, so that is a bad
            # response, not something to decode by guessing.
            raise FetchError("response body is not UTF-8 text", code="unexpected_response")
        return HttpResponse(
            status=int(raw.get("status", 0)),
            body=text,
            headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
        )

    def propose(self, batch: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        raise HostUnsupported("propose is not available in this host")
