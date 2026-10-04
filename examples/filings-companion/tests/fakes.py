"""In-memory Host for tests: scripted responses, storage, fake clock that advances on sleep."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from filings_companion.hostapi import FetchRequest, FetchResponse

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
# A syntactically valid contact used ONLY by tests. Product code never contains one.
CONTACT = "Jane Tester jane.tester@tester-mail.org"

Responder = FetchResponse | Callable[[FetchRequest], FetchResponse]


def ok(body: str, status: int = 200, headers: Mapping[str, str] | None = None) -> FetchResponse:
    return FetchResponse(status=status, headers=headers or {}, body=body)


class FakeHost:
    def __init__(self, routes=None, *, contact: str | None = CONTACT):
        self.routes = routes or {}
        self.requests: list[FetchRequest] = []
        self.request_times: list[float] = []
        self.storage: dict[str, str] = {} if contact is None else {"contact": contact}
        self._now = NOW
        self.slept = 0.0

    def fetch(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        self.request_times.append(self._now.timestamp())
        route = self.routes(request) if callable(self.routes) else self.routes[request.url]
        return route(request) if callable(route) else route

    def storage_get(self, key: str) -> str | None:
        return self.storage.get(key)

    def storage_set(self, key: str, value: str) -> None:
        self.storage[key] = value

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self._now += timedelta(seconds=seconds)
