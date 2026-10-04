"""Orchestration: fetch each source politely and assemble read-only series.

All I/O goes through the injected ``Host`` and ``Clock``. One failing series
never aborts the others; every failure is listed in ``issues`` with a stable
code. Nothing is guessed: a missing field is an explicit error.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from macro_context import ecb, sgfx, treasury, worldbank
from macro_context.errors import CoreError, ValidationError
from macro_context.host import Clock, Host, SystemClock, iso_utc
from macro_context.money import assert_no_floats
from macro_context.polite import OriginPolicy, PoliteClient
from macro_context.series import DISCLAIMER, Provenance, Series

REPORT_SCHEMA = "hellohq.macro-context@1"
SECTIONS = ("ecb", "worldbank", "treasury", "sgfx")
MAX_COUNTRIES = 6

# Our own conservative pacing. ECB, World Bank and Treasury publish no numeric
# limit, so we stay slow. data.gov.sg documents 4 requests / 10 s
# unauthenticated for Datastore Search; we use 3 / 10 s and >= 3 s apart.
DEFAULT_POLICIES: dict[str, OriginPolicy] = {
    ecb.ECB_HOST: OriginPolicy(max_calls=10, window_s=10.0, min_interval_s=1.0),
    worldbank.WB_HOST: OriginPolicy(max_calls=10, window_s=10.0, min_interval_s=1.0),
    treasury.TREASURY_HOST: OriginPolicy(max_calls=10, window_s=10.0, min_interval_s=1.0),
    sgfx.DATA_GOV_HOST: OriginPolicy(max_calls=3, window_s=10.0, min_interval_s=3.0),
}


@dataclass(frozen=True)
class ContextRequest:
    sections: tuple[str, ...] = SECTIONS
    ecb_series: tuple[str, ...] = tuple(s.id for s in ecb.CATALOG)
    worldbank_countries: tuple[str, ...] = worldbank.DEFAULT_COUNTRIES
    worldbank_indicators: tuple[str, ...] = tuple(s.code for s in worldbank.CATALOG)
    treasury_months: int = 12
    fx_currencies: tuple[str, ...] = sgfx.DEFAULT_CURRENCIES
    fx_months: int = 6


def _str_list(raw: Mapping[str, Any], name: str, default: tuple[str, ...], limit: int = 20) -> tuple[str, ...]:
    if name not in raw:
        return default
    value = raw[name]
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value) or len(value) > limit:
        raise ValidationError(f"{name} must be a non-empty list of up to {limit} strings")
    return tuple(value)


def parse_request(raw: Mapping[str, Any] | None) -> ContextRequest:
    raw = raw or {}
    known = {
        "sections",
        "ecb_series",
        "worldbank_countries",
        "worldbank_indicators",
        "treasury_months",
        "fx_currencies",
        "fx_months",
    }
    if set(raw) - known:
        raise ValidationError(f"unknown request fields: {sorted(set(raw) - known)}")
    sections = _str_list(raw, "sections", SECTIONS)
    if not set(sections) <= set(SECTIONS):
        raise ValidationError(f"sections must be a subset of {list(SECTIONS)}")
    ecb_ids = _str_list(raw, "ecb_series", ContextRequest.ecb_series)
    if not set(ecb_ids) <= set(ecb.CATALOG_BY_ID):
        raise ValidationError("unknown ecb_series id", code="unknown_series")
    countries = tuple(
        worldbank.validate_country(c)
        for c in _str_list(raw, "worldbank_countries", worldbank.DEFAULT_COUNTRIES, MAX_COUNTRIES)
    )
    indicators = _str_list(raw, "worldbank_indicators", ContextRequest.worldbank_indicators)
    if not set(indicators) <= set(worldbank.CATALOG_BY_CODE):
        raise ValidationError("unknown worldbank indicator", code="unknown_series")
    months: dict[str, int] = {}
    for name, default, high in (("treasury_months", 12, 60), ("fx_months", 6, 24)):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
            raise ValidationError(f"{name} must be an integer 1..{high}")
        months[name] = value
    return ContextRequest(
        sections=sections,
        ecb_series=ecb_ids,
        worldbank_countries=countries,
        worldbank_indicators=indicators,
        treasury_months=months["treasury_months"],
        fx_currencies=_str_list(raw, "fx_currencies", sgfx.DEFAULT_CURRENCIES, 10),
        fx_months=months["fx_months"],
    )


def _issue(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class _Run:
    def __init__(self, client: PoliteClient, clock: Clock) -> None:
        self.client, self.clock = client, clock
        self.series: list[Series] = []
        self.issues: list[dict[str, str]] = []

    def _prov(self, host: str, identifier: str, reference: str) -> Provenance:
        return Provenance(host, identifier, reference, iso_utc(self.clock.utcnow()))

    def run_ecb(self, ids: tuple[str, ...]) -> None:
        for spec_id in ids:
            spec = ecb.CATALOG_BY_ID[spec_id]
            try:
                response = self.client.get(ecb.url_for(spec))
                parsed = ecb.parse_response(response.body)
            except CoreError as exc:
                self.issues.append(_issue(exc.code, spec.id, exc.message))
                continue
            self.series.append(
                Series(
                    id=spec.id,
                    title=spec.title,
                    category=spec.category,
                    unit=parsed.unit_name or parsed.unit_code,
                    unit_basis="source",
                    unit_multiplier=parsed.unit_multiplier,
                    unit_note=spec.unit_note,
                    frequency=parsed.frequency or "unspecified",
                    observations=parsed.observations,
                    provenance=self._prov(ecb.ECB_HOST, f"{spec.flow}/{spec.key}", ecb.request_path(spec)),
                )
            )

    def run_worldbank(self, countries: tuple[str, ...], indicators: tuple[str, ...]) -> None:
        for country in countries:
            for code in indicators:
                spec = worldbank.CATALOG_BY_CODE[code]
                subject = f"{code}:{country}"
                try:
                    response = self.client.get(worldbank.url_for(country, code, 5))
                    parsed = worldbank.parse_response(response.body, country=country, indicator=code)
                except CoreError as exc:
                    self.issues.append(_issue(exc.code, subject, exc.message))
                    continue
                self.series.append(
                    Series(
                        id=f"wb.{code}.{country}",
                        title=parsed.title,
                        category=spec.category,
                        unit=spec.unit,
                        unit_basis="catalog",
                        unit_multiplier=None,
                        unit_note=spec.unit_note,
                        frequency=spec.frequency,
                        observations=parsed.observations,
                        provenance=self._prov(worldbank.WB_HOST, subject, worldbank.request_path(country, code, 5)),
                        country=country,
                        source_last_updated=parsed.last_updated,
                        truncated=parsed.truncated,
                    )
                )

    def run_treasury(self, months: int) -> None:
        since = (self.clock.utcnow() - timedelta(days=31 * months)).strftime("%Y-%m-%d")
        subject = "avg_interest_rates"
        try:
            response = self.client.get(treasury.url_for(since, 200))
            parsed = treasury.parse_response(response.body)
        except CoreError as exc:
            self.issues.append(_issue(exc.code, subject, exc.message))
            return
        for desc, observations in parsed.series.items():
            self.series.append(
                Series(
                    id=f"ust.avg_rate.{_slug(desc)}",
                    title=f"Average interest rate on outstanding marketable US {desc} (not a market yield)",
                    category="average_interest_rate",
                    unit="percent",
                    unit_basis="source",
                    unit_multiplier=None,
                    unit_note="meta.dataTypes declares PERCENTAGE; month-end values",
                    frequency="monthly",
                    observations=observations,
                    provenance=self._prov(
                        treasury.TREASURY_HOST, f"{subject}:{desc}", treasury.request_path(since, 200)
                    ),
                    truncated=parsed.truncated,
                )
            )

    def run_sgfx(self, currencies: tuple[str, ...], months: int) -> None:
        try:
            response = self.client.get(sgfx.url_for(100))
            parsed = sgfx.parse_response(response.body, currencies, months=months, limit=100)
        except CoreError as exc:
            self.issues.append(_issue(exc.code, sgfx.RESOURCE_ID, exc.message))
            return
        for name, observations in parsed.series.items():
            self.series.append(
                Series(
                    id=f"mas.fx.{_slug(name)}",
                    title=f"MAS exchange rate, SGD against {name} (as at end of month)",
                    category="fx",
                    unit="SGD per foreign-currency unit",
                    unit_basis="catalog",
                    unit_multiplier=None,
                    unit_note=sgfx.UNIT_NOTE,
                    frequency="monthly",
                    observations=observations,
                    provenance=self._prov(sgfx.DATA_GOV_HOST, f"{sgfx.RESOURCE_ID}:{name}", sgfx.request_path(100)),
                    truncated=parsed.truncated,
                )
            )


def fetch_context(
    host: Host,
    request: ContextRequest,
    clock: Clock | None = None,
    *,
    rand: Callable[[], float] = random.random,
    policies: Mapping[str, OriginPolicy] | None = None,
    cache_ttl_s: float = 3600.0,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    client = PoliteClient(host, clock, policies or DEFAULT_POLICIES, cache_ttl_s=cache_ttl_s, rand=rand)
    run = _Run(client, clock)
    if "ecb" in request.sections:
        run.run_ecb(request.ecb_series)
    if "worldbank" in request.sections:
        run.run_worldbank(request.worldbank_countries, request.worldbank_indicators)
    if "treasury" in request.sections:
        run.run_treasury(request.treasury_months)
    if "sgfx" in request.sections:
        run.run_sgfx(request.fx_currencies, request.fx_months)
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": iso_utc(clock.utcnow()),
        "disclaimer": DISCLAIMER,
        "series": [s.to_dict() for s in run.series],
        "issues": run.issues,
        "requests_sent": client.requests_sent,
    }
    assert_no_floats(report)
    return report
