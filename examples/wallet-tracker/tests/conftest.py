"""Shared test doubles. No test touches the network: sockets are blocked."""

from __future__ import annotations

import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from wallet_tracker.errors import FetchError, HostUnsupported
from wallet_tracker.host import HttpResponse


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*_a, **_k):
        raise AssertionError("network access attempted in a unit test")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


class FakeClock:
    """Deterministic clock: ``sleep`` advances time and records the request."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []
        self._start = datetime(2026, 10, 4, 6, 0, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.t += seconds

    def utcnow(self) -> datetime:
        return self._start + timedelta(seconds=self.t - 1000.0)


Handler = Callable[[str, str, str], "HttpResponse | Exception | None"]


class FakeHost:
    """Records every fetch/propose. ``handler(method, url, body)`` decides replies.

    ``propose_script`` scripts the host's answer to each ``propose`` call in order: a
    list of receipt dicts, or an exception to raise. Calls past the end of the script
    (and every call when it is ``None``) answer ``queued`` for every proposal.
    """

    def __init__(
        self,
        handler: Handler | None = None,
        *,
        supports_propose: bool = True,
        propose_script: list[Any] | None = None,
    ) -> None:
        self.handler = handler or (lambda m, u, b: None)
        self.calls: list[tuple[str, str, Mapping[str, str], str]] = []
        self.batches: list[Mapping[str, Any]] = []
        self.supports_propose = supports_propose
        self.propose_script = list(propose_script or [])

    def fetch(self, method, url, headers=None, body=""):
        self.calls.append((method, url, dict(headers or {}), body))
        out = self.handler(method, url, body)
        if out is None:
            raise FetchError(f"no fake response for {method} {url}", code="permission_denied")
        if isinstance(out, Exception):
            raise out
        return out

    def propose(self, batch):
        if not self.supports_propose:
            raise HostUnsupported("propose is not available")
        self.batches.append(batch)
        if self.propose_script:
            step = self.propose_script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        return [{"index": i, "outcome": "queued"} for i, _ in enumerate(batch["proposals"])]


def ok(body: str, **headers: str) -> HttpResponse:
    return HttpResponse(200, body, headers)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
