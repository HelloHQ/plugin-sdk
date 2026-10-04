"""Rate limiting and jittered backoff, driven by an injected ``Clock``."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from wallet_tracker.host import Clock


class SlidingWindowLimiter:
    """At most ``max_calls`` per ``window_s`` AND >= ``min_interval_s`` apart.

    ``acquire`` blocks (via ``clock.sleep``) until a call is allowed, then
    records it. Requests are therefore sequential and paced, never bursty.
    """

    def __init__(self, clock: Clock, max_calls: int, window_s: float, min_interval_s: float = 0.0) -> None:
        if max_calls < 1 or window_s <= 0 or min_interval_s < 0:
            raise ValueError("invalid limiter configuration")
        self._clock = clock
        self._max = max_calls
        self._window = window_s
        self._min_interval = min_interval_s
        self._stamps: deque[float] = deque()

    def acquire(self) -> float:
        """Wait as long as needed; return total seconds waited."""
        waited = 0.0
        while True:
            now = self._clock.monotonic()
            while self._stamps and now - self._stamps[0] >= self._window:
                self._stamps.popleft()
            wait = 0.0
            if self._stamps:
                wait = max(wait, self._min_interval - (now - self._stamps[-1]))
            if len(self._stamps) >= self._max:
                wait = max(wait, self._window - (now - self._stamps[0]))
            if wait <= 0:
                self._stamps.append(now)
                return waited
            self._clock.sleep(wait)
            waited += wait


def backoff_delay(
    attempt: int,
    *,
    base_s: float,
    cap_s: float,
    rand: Callable[[], float],
    retry_after_s: float | None = None,
) -> float:
    """Exponential backoff with full jitter.

    ``delay = rand() * min(cap, base * 2**attempt)`` (attempt starts at 0).
    A server ``Retry-After`` (seconds) is a lower bound, itself capped at
    ``cap_s`` so a hostile header cannot stall the plugin.
    """
    ceiling = min(cap_s, base_s * (2**attempt))
    delay = rand() * ceiling
    if retry_after_s is not None and retry_after_s > 0:
        delay = max(delay, min(retry_after_s, cap_s))
    return delay


def parse_retry_after(value: str | None) -> float | None:
    """Seconds form only. HTTP-date form is deliberately ignored (-> None)."""
    if value is None:
        return None
    text = value.strip()
    if not text.isdigit():
        return None
    return float(int(text))
