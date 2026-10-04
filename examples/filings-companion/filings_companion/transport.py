"""Fetching through the Host: origin allowlist, per-service rate limits, User-Agent, caps."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from .contact import get_contact, user_agent
from .errors import (
    HttpError,
    OriginNotAllowed,
    ParseError,
    RateLimited,
    ResponseTooLarge,
    ValidationError,
)
from .hostapi import FetchRequest, FetchResponse, Host
from .ratelimit import SlidingWindowLimiter

# Exact hostnames; must equal manifest.json (a test enforces it).
SEC_DATA_ORIGIN = "data.sec.gov"
SEC_WWW_ORIGIN = "www.sec.gov"  # only for the documented company_tickers.json file
OPENFIGI_ORIGIN = "api.openfigi.com"
ALLOWED_ORIGINS = (SEC_DATA_ORIGIN, SEC_WWW_ORIGIN, OPENFIGI_ORIGIN)
SEC_ORIGINS = (SEC_DATA_ORIGIN, SEC_WWW_ORIGIN)

SEC_HARD_LIMIT_PER_S = 10  # the SEC's published maximum
SEC_PER_S = 8  # we stay below it
OPENFIGI_PER_MIN = 25  # documented keyless limit
OPENFIGI_WINDOW_S = 60.5  # padded so window alignment can never put us over
MAX_BODY_CHARS = 8 * 1024 * 1024  # mirrors the host's 8 MiB response cap


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise OriginNotAllowed("only https URLs are allowed")
    if parts.username or parts.password:
        raise OriginNotAllowed("credentials in URLs are not allowed")
    if parts.port not in (None, 443):
        raise OriginNotAllowed("non-default ports are not allowed")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_ORIGINS:
        raise OriginNotAllowed(f"origin not declared in the manifest: {host or '(none)'}")
    return host


class Fetcher:
    """Owns the limiters (so limits hold across calls) and adds the SEC User-Agent."""

    def __init__(self, host: Host, *, sec_per_second: int = SEC_PER_S) -> None:
        if not 1 <= sec_per_second <= SEC_HARD_LIMIT_PER_S:
            raise ValidationError(f"sec_per_second must be 1..{SEC_HARD_LIMIT_PER_S}")
        self._host = host
        clock = lambda: host.now().timestamp()  # noqa: E731
        self._sec = SlidingWindowLimiter(sec_per_second, 1.0, clock=clock, sleep=host.sleep)
        self._figi = SlidingWindowLimiter(
            OPENFIGI_PER_MIN, OPENFIGI_WINDOW_S, clock=clock, sleep=host.sleep
        )
        self.requests_made = 0

    def request(
        self,
        url: str,
        *,
        accept: str = "application/json",
        method: str = "GET",
        body: str | None = None,
    ) -> FetchResponse:
        origin = origin_of(url)
        headers = {"Accept": accept}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin in SEC_ORIGINS:
            # Refuses (ContactNotConfigured) before any SEC request if no valid contact.
            headers["User-Agent"] = user_agent(get_contact(self._host))
            self._sec.acquire()
        else:
            self._figi.acquire()  # no User-Agent/contact is sent to third parties
        self.requests_made += 1
        resp = self._host.fetch(FetchRequest(url=url, method=method, headers=headers, body=body))
        return _check(resp, origin)


def _check(resp: FetchResponse, origin: str) -> FetchResponse:
    if resp.status in (301, 302, 303, 307, 308):
        raise HttpError("unexpected redirect (not followed)", status=resp.status)
    if resp.status == 429:
        reset = _header(resp.headers, "ratelimit-reset")
        raise RateLimited(
            "the source asked us to slow down (429)",
            retry_after_s=int(reset) if reset and reset.isdigit() else None,
        )
    if resp.status == 403 and origin in SEC_ORIGINS:
        raise HttpError(
            "SEC refused the request (403): check that your contact in the User-Agent is valid",
            status=403,
            code="sec_forbidden",
        )
    if resp.status >= 400:
        raise HttpError(f"source returned HTTP {resp.status}", status=resp.status)
    if len(resp.body) > MAX_BODY_CHARS:
        raise ResponseTooLarge("response larger than the host cap", status=resp.status)
    return resp


def _header(headers: Any, name: str) -> str | None:
    for key, value in dict(headers).items():
        if str(key).lower() == name:
            return str(value)
    return None


def loads_decimal(text: str, *, what: str) -> Any:
    """Parse JSON with every non-integer number as Decimal (never binary float)."""
    try:
        return json.loads(text, parse_float=Decimal)
    except (ValueError, RecursionError) as exc:
        raise ParseError(f"{what}: response is not valid JSON") from exc
