"""The narrow Host interface this plugin needs, plus request/response value types.

No ``propose`` operation: per the plugin roadmap this plugin writes nothing to the ledger.
It only reads public filings and shows them. The person's holdings are NOT readable by any
plugin, so identifiers come from the person (typed in, or kept in plugin storage).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


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


class Host(Protocol):
    def fetch(self, request: FetchRequest) -> FetchResponse:
        """HTTPS fetch from a manifest-declared origin. No redirects are followed."""

    def storage_get(self, key: str) -> str | None:
        """Read from the plugin's private key-value store (plugin:storage)."""

    def storage_set(self, key: str, value: str) -> None:
        """Write to the plugin's private key-value store (plugin:storage)."""

    def now(self) -> datetime:
        """Timezone-aware current time (UTC)."""

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds`` (used only by rate limiters)."""
