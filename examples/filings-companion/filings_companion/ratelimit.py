"""Client-side sliding-window rate limiter with an injectable clock and sleep."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from .errors import ValidationError


class SlidingWindowLimiter:
    """Allow at most ``max_calls`` acquisitions in any ``window_s`` seconds.

    ``clock`` returns seconds as a float; ``sleep`` blocks for the given seconds. Both are
    injected so tests run on a fake clock with no real waiting.
    """

    def __init__(
        self,
        max_calls: int,
        window_s: float,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if max_calls < 1 or window_s <= 0:
            raise ValidationError("limiter needs max_calls >= 1 and window_s > 0")
        self.max_calls = max_calls
        self.window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()

    def acquire(self) -> float:
        """Block until a slot is free. Returns the total seconds waited."""
        waited = 0.0
        while True:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= self.window_s:
                self._stamps.popleft()
            if len(self._stamps) < self.max_calls:
                self._stamps.append(now)
                return waited
            delay = self.window_s - (now - self._stamps[0])
            delay = max(delay, 0.001)
            self._sleep(delay)
            waited += delay
