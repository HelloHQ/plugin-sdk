"""MAS exchange rates, via the data.gov.sg datastore mirror.

Dataset: "Exchange Rates, (As At End Of Period), Monthly" published by the
Monetary Authority of Singapore on data.gov.sg,
``resource_id=d_cdd73fd4341b345fa4307e44d6f82175``
(https://data.gov.sg/datasets/d_cdd73fd4341b345fa4307e44d6f82175/view).

* Endpoint: ``https://data.gov.sg/api/action/datastore_search?resource_id=...``
  (CKAN-style; the developer guide at guide.data.gov.sg lists "Datastore
  Search" with a 4 requests / 10 s unauthenticated limit).
* Layout (from the dataset page): WIDE - one row per currency (``DataSeries``)
  and one column per month (``2026Jul``, ``2026Jun``, ...).
* Not found in the documentation reviewed: the exact JSON field list of a
  datastore_search reply and per-currency quoting units. The parser relies on
  ``success``, ``result.records[]``, ``DataSeries`` and ``YYYYMon`` columns,
  and is strict about them.

Units: the dataset does not state whether a currency is quoted per 1 or per
100 units, and MAS's own page lists several currencies "per 100 units" that
the dataset's magnitudes (observed 2026-10-04) do not match. The plugin
therefore NEVER converts: it emits the published number with
``unit_multiplier = null`` and a note. Callers must not treat the value as
SGD per 1 unit without checking MAS.

NOTE: the older dataset d_046ff8d521a218d9178178cfbfc45c2c (SGD per USD,
daily) stops in October 2015 and is deliberately not used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from macro_context.errors import ParseError
from macro_context.money import loads_decimal, to_decimal_string
from macro_context.series import Observation

DATA_GOV_HOST = "data.gov.sg"
RESOURCE_ID = "d_cdd73fd4341b345fa4307e44d6f82175"
_MONTHS = {
    m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)
}
_PERIOD = re.compile(r"^(\d{4})([A-Z][a-z]{2})$")
DEFAULT_CURRENCIES = ("US Dollar", "Euro", "Sterling Pound", "Japanese Yen", "Renminbi")
UNIT_NOTE = (
    "SGD per foreign-currency unit as published; the dataset does not state whether the quote is per 1 or per 100 "
    "units, so no conversion is applied. Verify against MAS before converting."
)


def request_path(limit: int = 100) -> str:
    if not 1 <= limit <= 1000:
        raise ValueError("invalid limit")
    return f"/api/action/datastore_search?resource_id={RESOURCE_ID}&limit={limit}"


def url_for(limit: int = 100) -> str:
    return f"https://{DATA_GOV_HOST}{request_path(limit)}"


def _period(column: str) -> str | None:
    match = _PERIOD.match(column)
    if not match or match.group(2) not in _MONTHS:
        return None
    return f"{match.group(1)}-{_MONTHS[match.group(2)]:02d}"


@dataclass(frozen=True)
class ParsedFx:
    series: dict[str, tuple[Observation, ...]]  # DataSeries name -> ascending observations
    truncated: bool


def parse_response(text: str, currencies: tuple[str, ...], *, months: int = 6, limit: int = 100) -> ParsedFx:
    data = loads_decimal(text)
    if not isinstance(data, dict) or data.get("success") is not True:
        raise ParseError("data.gov.sg response: success is not true")
    result = data.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("records"), list):
        raise ParseError("data.gov.sg response: missing result.records")
    total = result.get("total")
    truncated = isinstance(total, int) and not isinstance(total, bool) and total > limit
    rows: dict[str, dict] = {}
    for i, rec in enumerate(result["records"]):
        if not isinstance(rec, dict) or not isinstance(rec.get("DataSeries"), str):
            raise ParseError(f"records[{i}]: missing DataSeries")
        rows[rec["DataSeries"].strip()] = rec
    out: dict[str, tuple[Observation, ...]] = {}
    for currency in currencies:
        rec = rows.get(currency)
        if rec is None:
            raise ParseError(f"currency {currency!r} not present in the dataset", code="series_not_found")
        candidates: list[tuple[str, object]] = []
        for column, raw in rec.items():
            period = _period(column)
            if period is None or raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            candidates.append((period, raw))
        if not candidates:
            raise ParseError(f"currency {currency!r} has no observations", code="no_data")
        candidates.sort(key=lambda c: c[0])
        # Only the latest `months` columns are converted: pre-2006 history is text in this dataset.
        out[currency] = tuple(
            Observation(p, to_decimal_string(raw, f"{currency}.{p}")) for p, raw in candidates[-months:]
        )
    return ParsedFx(out, truncated)
