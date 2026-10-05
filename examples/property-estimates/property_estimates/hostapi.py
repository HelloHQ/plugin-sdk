"""The narrow Host interface this plugin needs, plus request/response value types.

The real adapter (``plugin.py``) maps ``fetch`` onto the SDK's ``host.fetch`` and ``propose``
onto ``host.propose`` (propose-only writes: the person approves each suggestion in the app).
Tests use an in-memory fake.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class FetchRequest:
    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: Mapping[str, str]
    body: str


@dataclass(frozen=True)
class Receipt:
    index: int
    outcome: str
    reason: str | None = None


class Host(Protocol):
    def fetch(self, request: FetchRequest) -> FetchResponse:
        """HTTPS fetch from a manifest-declared origin. No redirects are followed."""

    def propose(self, proposals: Sequence[Mapping[str, Any]]) -> list[Receipt]:
        """Submit propose-only values; one receipt per proposal, in order.

        Raises ``PendingHostSupport`` when the host has no ``propose`` and ``ProposeRefused``
        when the host refuses the whole call.
        """

    def now(self) -> datetime:
        """Timezone-aware current time (UTC)."""

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds`` (used only by rate limiters)."""
