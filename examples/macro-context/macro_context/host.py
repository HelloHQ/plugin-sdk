"""The narrow ``Host`` interface the plugin core depends on.

The core never imports the SDK and never touches a socket. It needs exactly:

* ``fetch``   - an HTTPS request through the host, which enforces the
  ``network:fetch`` origin allowlist (``Host.fetch`` in production is
  ``hellohq_plugin_sdk.host.fetch``);

This plugin is READ-ONLY: it holds no ``propose:*`` or write permission and the
interface has no write operation at all.

``Clock`` is injected so rate-limit and backoff logic is testable with a fake
clock and never really sleeps in tests.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


class Host(Protocol):
    def fetch(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: str = "",
    ) -> HttpResponse: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def utcnow(self) -> datetime:
        return datetime.now(UTC)


def iso_utc(moment: datetime) -> str:
    """RFC 3339 UTC with second precision, e.g. ``2026-10-04T06:00:02Z``."""
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
