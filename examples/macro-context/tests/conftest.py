"""Shared test doubles. No test touches the network: sockets are blocked."""

from __future__ import annotations

import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from macro_context.errors import FetchError
from macro_context.host import HttpResponse


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
    """Records every fetch. ``handler(method, url, body)`` decides replies."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler = handler or (lambda m, u, b: None)
        self.calls: list[tuple[str, str, Mapping[str, str], str]] = []

    def fetch(self, method, url, headers=None, body=""):
        self.calls.append((method, url, dict(headers or {}), body))
        out = self.handler(method, url, body)
        if out is None:
            raise FetchError(f"no fake response for {method} {url}", code="permission_denied")
        if isinstance(out, Exception):
            raise out
        return out


def ok(body: str, **headers: str) -> HttpResponse:
    return HttpResponse(200, body, headers)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
