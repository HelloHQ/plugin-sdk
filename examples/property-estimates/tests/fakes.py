"""In-memory Host for tests: scripted responses, fake clock that advances on sleep."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from property_estimates.hostapi import FetchRequest, FetchResponse, Receipt

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)

Responder = FetchResponse | Callable[[FetchRequest], FetchResponse]


def ok(body: str, status: int = 200, headers: Mapping[str, str] | None = None) -> FetchResponse:
    return FetchResponse(status=status, headers=headers or {}, body=body)


class FakeHost:
    def __init__(self, routes: Mapping[str, Responder] | Callable[[str], Responder] | None = None):
        self.routes = routes or {}
        self.requests: list[FetchRequest] = []
        self.proposed: list[Sequence[Mapping[str, Any]]] = []
        self.propose_supported = False
        self._now = NOW
        self.slept = 0.0

    def fetch(self, request: FetchRequest) -> FetchResponse:
        self.requests.append(request)
        route = self.routes(request.url) if callable(self.routes) else self.routes[request.url]
        return route(request) if callable(route) else route

    def propose(self, proposals: Sequence[Mapping[str, Any]]) -> list[Receipt]:
        if not self.propose_supported:
            raise NotImplementedError
        self.proposed.append(proposals)
        return [Receipt(index=i, outcome="queued") for i, _ in enumerate(proposals)]

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self._now += timedelta(seconds=seconds)
