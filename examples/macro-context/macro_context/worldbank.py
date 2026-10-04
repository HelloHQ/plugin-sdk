"""World Bank Indicators API v2 (no key, no registration).

Documented: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation
(``/v2/country/{country}/indicator/{indicator}``, ``format=json``, ``mrv`` =
most recent values, ``per_page``). The response is a two-element array: paging
metadata, then the observations. The docs page does not publish the field
list of an observation or the error body; this parser relies on the fields
seen in practice when it was written (``indicator{id,value}``,
``countryiso3code``, ``date``, ``value``) and is strict about them. An error
reply (first element carrying ``message``) raises ``WorldBankError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from macro_context.errors import CoreError, ParseError, ValidationError
from macro_context.money import loads_decimal, to_decimal_string
from macro_context.series import Observation

WB_HOST = "api.worldbank.org"
_COUNTRY = re.compile(r"^[A-Za-z]{3}$")  # ISO 3166-1 alpha-3, or a WB aggregate code such as EMU / WLD
_INDICATOR = re.compile(r"^[A-Z0-9]{2,6}(\.[A-Z0-9]{2,12}){1,4}$")
_YEAR = re.compile(r"^\d{4}$")


class WorldBankError(CoreError):
    code = "worldbank_error"


@dataclass(frozen=True)
class IndicatorSpec:
    code: str
    category: str
    unit: str  # from the indicator's own name "(annual %)"
    unit_note: str
    frequency: str = "annual"


CATALOG: tuple[IndicatorSpec, ...] = (
    IndicatorSpec(
        "FP.CPI.TOTL.ZG", "inflation", "percent", "annual % change in consumer prices, per the indicator name"
    ),
    IndicatorSpec("NY.GDP.MKTP.KD.ZG", "growth", "percent", "annual % growth of real GDP, per the indicator name"),
)
CATALOG_BY_CODE = {s.code: s for s in CATALOG}
DEFAULT_COUNTRIES = ("USA", "SGP", "CHN", "EMU")


def validate_country(code: str) -> str:
    if not isinstance(code, str) or not _COUNTRY.match(code.strip()):
        raise ValidationError(f"country must be a 3-letter code, got {code!r}", code="bad_country")
    return code.strip().upper()


def request_path(country: str, indicator: str, mrv: int) -> str:
    if not _COUNTRY.match(country) or not _INDICATOR.match(indicator) or not 1 <= mrv <= 60:
        raise ValueError("invalid World Bank request")
    return f"/v2/country/{country}/indicator/{indicator}?format=json&mrv={mrv}&per_page={mrv}"


def url_for(country: str, indicator: str, mrv: int) -> str:
    return f"https://{WB_HOST}{request_path(country, indicator, mrv)}"


@dataclass(frozen=True)
class ParsedIndicator:
    observations: tuple[Observation, ...]
    title: str
    last_updated: str | None
    truncated: bool


def parse_response(text: str, *, country: str, indicator: str) -> ParsedIndicator:
    data = loads_decimal(text)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ParseError("worldbank response: expected a two-element array")
    meta = data[0]
    if "message" in meta:
        message = meta["message"]
        detail = ""
        if isinstance(message, list) and message and isinstance(message[0], dict):
            detail = str(message[0].get("value", ""))[:120]
        raise WorldBankError(f"World Bank API error: {detail or 'unspecified'}")
    if len(data) != 2:
        raise ParseError("worldbank response: expected a two-element array")
    rows = data[1]
    if rows is None:
        raise ParseError("worldbank response: no observations", code="no_data")
    if not isinstance(rows, list):
        raise ParseError("worldbank response: observations must be an array")
    pages = meta.get("pages")
    truncated = isinstance(pages, int) and not isinstance(pages, bool) and pages > 1
    updated = meta.get("lastupdated")
    title: str | None = None
    points: list[Observation] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ParseError(f"observation[{i}]: expected an object")
        ind = row.get("indicator")
        if not isinstance(ind, dict) or ind.get("id") != indicator or not isinstance(ind.get("value"), str):
            raise ParseError(f"observation[{i}]: indicator does not match the request")
        title = ind["value"]
        iso3 = row.get("countryiso3code")
        if not isinstance(iso3, str) or iso3.upper() != country.upper():
            raise ParseError(f"observation[{i}]: country does not match the request")
        date = row.get("date")
        if not isinstance(date, str) or not _YEAR.match(date):
            raise ParseError(f"observation[{i}]: expected an annual date (YYYY)")
        if "value" not in row:
            raise ParseError(f"observation[{i}]: missing value")
        if row["value"] is None:
            continue  # the Bank publishes explicit nulls for years without data
        points.append(Observation(date, to_decimal_string(row["value"], f"observation[{i}].value", allow_str=False)))
    if not points or title is None:
        raise ParseError("worldbank response: all observations are empty", code="no_data")
    points.sort(key=lambda o: o.period)
    return ParsedIndicator(
        observations=tuple(points),
        title=title,
        last_updated=updated if isinstance(updated, str) else None,
        truncated=truncated,
    )
