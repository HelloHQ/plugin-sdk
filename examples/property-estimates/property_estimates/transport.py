"""Fetching through the Host with an origin allowlist, per-origin rate limits and size caps."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlsplit

from .errors import HttpError, OriginNotAllowed, ParseError, RateLimited
from .hostapi import FetchRequest, FetchResponse, Host
from .ratelimit import SlidingWindowLimiter

# Must equal the origins in manifest.json (a test enforces it). Exact hostnames, no wildcards.
SG_ORIGIN = "data.gov.sg"
UK_ORIGIN = "landregistry.data.gov.uk"
FR_ORIGIN = "files.data.gouv.fr"
FR_STORAGE_ORIGIN = "geo-dvf.s3.sbg.io.cloud.ovh.net"  # files.data.gouv.fr 302s here
ALLOWED_ORIGINS = (SG_ORIGIN, UK_ORIGIN, FR_ORIGIN, FR_STORAGE_ORIGIN)

# (max calls, window seconds). data.gov.sg: 4 requests / 10 s unauthenticated (documented);
# the window is padded to 10.5 s so clock skew cannot push us over. The others publish no
# limit, so we self-impose a courtesy limit.
LIMITS: dict[str, tuple[int, float]] = {
    SG_ORIGIN: (4, 10.5),
    UK_ORIGIN: (2, 1.0),
    FR_ORIGIN: (2, 1.0),
    FR_STORAGE_ORIGIN: (2, 1.0),
}
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


def normalize_url(url: str) -> str:
    """Drop an explicit :443 so the host sees a plain https URL."""
    parts = urlsplit(url)
    if parts.port == 443 and not parts.username and not parts.password:
        return parts._replace(netloc=parts.hostname or "").geturl()
    return url


class Fetcher:
    """GET helper used by every source. Owns the limiters, so limits hold across calls."""

    def __init__(self, host: Host) -> None:
        self._host = host
        clock = lambda: host.now().timestamp()  # noqa: E731
        self._limiters = {
            origin: SlidingWindowLimiter(n, w, clock=clock, sleep=host.sleep)
            for origin, (n, w) in LIMITS.items()
        }
        self.requests_made = 0

    def get(self, url: str, *, accept: str, max_redirects: int = 1) -> FetchResponse:
        current = normalize_url(url)
        for _ in range(max_redirects + 1):
            origin = origin_of(current)
            self._limiters[origin].acquire()
            self.requests_made += 1
            resp = self._host.fetch(FetchRequest(url=current, headers={"Accept": accept}))
            if resp.status in (301, 302, 303, 307, 308):
                location = _header(resp.headers, "location")
                if not location:
                    raise HttpError("redirect without Location", status=resp.status)
                current = normalize_url(urljoin(current, location))  # re-checked on next loop
                continue
            if resp.status == 429:
                raise RateLimited("the source asked us to slow down (429)", status=429)
            if resp.status >= 400:
                raise HttpError(f"source returned HTTP {resp.status}", status=resp.status)
            if len(resp.body) > MAX_BODY_CHARS:
                raise HttpError("response larger than the host cap", status=resp.status)
            return resp
        raise HttpError("too many redirects", status=310)


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


def window_start(as_of: date, months: int) -> date:
    """First day of the calendar month ``months`` months before ``as_of``'s month."""
    index = as_of.year * 12 + (as_of.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)
