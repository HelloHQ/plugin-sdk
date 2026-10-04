"""Output model: read-only context series with as-of dates, units and provenance.

Information only - never advice, never a forecast. Values are reproduced as
published; the plugin computes nothing from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DISCLAIMER = "Context data as published by the named source. Information only: not advice, not a forecast."

CATEGORIES = ("policy_rate", "fx", "inflation", "growth", "yield", "average_interest_rate")


@dataclass(frozen=True)
class Observation:
    period: str  # ISO date (2026-10-02), month (2026-07) or year (2025), as published
    value: str  # exact decimal string, digit-for-digit as published


@dataclass(frozen=True)
class Provenance:
    source: str  # host contacted, e.g. "data-api.ecb.europa.eu"
    identifier: str  # series key / indicator / dataset id used
    reference: str  # request path (+ query) that produced the data
    fetched_at: str  # RFC 3339 UTC

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "identifier": self.identifier,
            "reference": self.reference,
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True)
class Series:
    id: str
    title: str
    category: str
    unit: str  # as stated by the source where it states one (see unit_basis)
    unit_basis: str  # "source" | "catalog"
    unit_multiplier: int | None  # None = the source does not say
    unit_note: str | None
    frequency: str
    observations: tuple[Observation, ...]  # ascending by period, never empty
    provenance: Provenance
    country: str | None = None
    source_last_updated: str | None = None
    truncated: bool = False

    @property
    def as_of(self) -> str:
        return self.observations[-1].period

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "unit": self.unit,
            "unit_basis": self.unit_basis,
            "unit_multiplier": self.unit_multiplier,
            "frequency": self.frequency,
            "as_of": self.as_of,
            "latest": self.observations[-1].value,
            "observations": [{"period": o.period, "value": o.value} for o in self.observations],
            "provenance": self.provenance.to_dict(),
        }
        if self.unit_note:
            out["unit_note"] = self.unit_note
        if self.country:
            out["country"] = self.country
        if self.source_last_updated:
            out["source_last_updated"] = self.source_last_updated
        if self.truncated:
            out["truncated"] = True
        return out
