"""Production ``Host`` adapter over ``hellohq_plugin_sdk`` (Tier-1 sidecar)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from wallet_tracker.errors import FetchError, HostError, HostUnsupported, ProposeRefused
from wallet_tracker.host import HttpResponse


class SdkHost:
    """Maps the narrow ``Host`` onto the Python SDK.

    ``fetch`` is ``network:fetch`` (Verified tier, Tier 1). ``propose`` is the
    propose-only write (``propose:holdings`` / ``propose:valuations``): the
    person approves each suggestion in the app, and the plugin only learns a
    receipt per proposal. When the host (or the installed SDK) has no
    ``propose`` it raises ``HostUnsupported`` and the caller returns the
    proposals as data instead.
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
        try:
            from hellohq_plugin_sdk import PluginError
            from hellohq_plugin_sdk import host as sdk
            from hellohq_plugin_sdk.proposals import ProposeError, ProposeUnsupported
        except ImportError as exc:  # an SDK older than 0.2.0 has no propose
            raise HostUnsupported("the installed hellohq-plugin-sdk has no propose (needs >= 0.2.0)") from exc

        try:
            receipts = sdk.propose(batch)
        except ProposeUnsupported as exc:
            raise HostUnsupported("the host does not support propose") from exc
        except ProposeError as exc:
            raise ProposeRefused(exc.message, code=exc.code, reason=exc.reason, retryable=exc.retryable) from exc
        except PluginError as exc:  # the host closed the pipe, or replied with nonsense
            raise HostError(str(exc), code=getattr(exc, "code", "host_error")) from exc
        return [
            {"index": r.index, "outcome": str(r.outcome), **({"reason": r.reason} if r.reason else {})}
            for r in receipts
        ]
