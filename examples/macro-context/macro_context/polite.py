"""A sequential, cached, rate-limited, retrying client on top of ``Host.fetch``."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from macro_context.errors import FetchError, RateLimitedError, ResponseTooLarge
from macro_context.host import Clock, Host, HttpResponse
from macro_context.ratelimit import SlidingWindowLimiter, backoff_delay, parse_retry_after

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRYABLE_CODES = frozenset({"timeout", "network", "rate_limit_exceeded", "host_error", "fetch_error"})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # mirrors the host's own 8 MiB cap


@dataclass(frozen=True)
class OriginPolicy:
    max_calls: int
    window_s: float
    min_interval_s: float = 0.0


class PoliteClient:
    """Every request: allowlist check -> cache -> pace -> send -> retry/backoff.

    * Only hosts present in ``policies`` may be contacted, and only over HTTPS
      (defence in depth: the host enforces the manifest allowlist as well).
    * Strictly sequential: one in-flight request at a time (the SDK is
      synchronous), paced by a per-origin ``SlidingWindowLimiter``.
    * Successful (2xx) responses are cached in memory for ``cache_ttl_s``.
    * 429/5xx and transport errors are retried up to ``max_retries`` times
      with full-jitter exponential backoff; ``Retry-After`` (seconds) is
      honoured as a lower bound.
    * No API keys, cookies or auth headers are ever sent.
    """

    def __init__(
        self,
        host: Host,
        clock: Clock,
        policies: Mapping[str, OriginPolicy],
        *,
        cache_ttl_s: float = 60.0,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
        rand: Callable[[], float] = random.random,
        max_cache_entries: int = 256,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._host = host
        self._clock = clock
        self._limiters = {
            origin: SlidingWindowLimiter(clock, p.max_calls, p.window_s, p.min_interval_s)
            for origin, p in policies.items()
        }
        self._ttl = cache_ttl_s
        self._max_retries = max_retries
        self._base = backoff_base_s
        self._cap = backoff_cap_s
        self._rand = rand
        self._max_cache = max_cache_entries
        self._max_bytes = max_response_bytes
        self._cache: dict[str, tuple[float, HttpResponse]] = {}
        self.requests_sent = 0

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        return self._request("GET", url, headers, "")

    def post_json(self, url: str, body: str) -> HttpResponse:
        return self._request("POST", url, {"Content-Type": "application/json", "Accept": "application/json"}, body)

    def _request(self, method: str, url: str, headers: Mapping[str, str] | None, body: str) -> HttpResponse:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise FetchError("only https URLs are allowed", code="origin_not_allowed")
        limiter = self._limiters.get(parts.hostname.lower())
        if limiter is None:
            raise FetchError("origin is not on this plugin's allowlist", code="origin_not_allowed")

        key = hashlib.sha256(f"{method}\n{url}\n{body}".encode()).hexdigest()
        now = self._clock.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]

        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            limiter.acquire()
            self.requests_sent += 1
            retry_after: float | None = None
            try:
                response = self._host.fetch(method, url, headers, body)
            except FetchError as exc:
                if exc.code not in RETRYABLE_CODES:
                    raise
                last = exc
            else:
                if len(response.body.encode("utf-8", "replace")) > self._max_bytes:
                    raise ResponseTooLarge("response exceeds the size cap")
                if 200 <= response.status < 300:
                    self._store(key, response)
                    return response
                if response.status not in RETRYABLE_STATUS:
                    raise FetchError(f"HTTP {response.status}", code=f"http_{response.status}")
                retry_after = parse_retry_after(response.header("retry-after"))
                last = FetchError(f"HTTP {response.status}", code=f"http_{response.status}")
            if attempt < self._max_retries:
                self._clock.sleep(
                    backoff_delay(
                        attempt, base_s=self._base, cap_s=self._cap, rand=self._rand, retry_after_s=retry_after
                    )
                )
        raise RateLimitedError(f"gave up after {self._max_retries + 1} attempts: {last}") from last

    def _store(self, key: str, response: HttpResponse) -> None:
        if len(self._cache) >= self._max_cache:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (self._clock.monotonic(), response)
