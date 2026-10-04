"""US Treasury Fiscal Data API (no registration, no token).

Documented: https://fiscaldata.treasury.gov/api-documentation/ - base URL
``https://api.fiscaldata.treasury.gov/services/api/fiscal_service``; query
parameters ``fields``, ``filter`` (operators lt, lte, gt, gte, eq, in),
``sort``, ``format``, ``page[number]``, ``page[size]``; response
``{data[], meta{count, labels, dataTypes, dataFormats, total-count,
total-pages}, links}``; "all response fields are treated as strings".

Dataset used: ``v2/accounting/od/avg_interest_rates`` ("Average Interest Rates
on U.S. Treasury Securities"). These are AVERAGE interest rates on
outstanding marketable securities as of each month end - NOT market yields
and NOT a par yield curve. Fiscal Data does not provide a daily par yield
curve; that is published separately by Treasury (see README, open question).
The unit comes from the source: ``meta.dataTypes`` must say PERCENTAGE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from macro_context.errors import ParseError
from macro_context.money import loads_decimal, to_decimal_string
from macro_context.series import Observation

TREASURY_HOST = "api.fiscaldata.treasury.gov"
ENDPOINT = "/services/api/fiscal_service/v2/accounting/od/avg_interest_rates"
FIELDS = ("record_date", "security_type_desc", "security_desc", "avg_interest_rate_amt")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def request_path(since: str, page_size: int) -> str:
    if not _DATE.match(since) or not 1 <= page_size <= 1000:
        raise ValueError("invalid Treasury request")
    # Square brackets are percent-encoded, as the API itself does in its links.
    return (
        f"{ENDPOINT}?fields={','.join(FIELDS)}"
        f"&filter=record_date:gte:{since},security_type_desc:eq:Marketable"
        f"&sort=-record_date&page%5Bsize%5D={page_size}"
    )


def url_for(since: str, page_size: int) -> str:
    return f"https://{TREASURY_HOST}{request_path(since, page_size)}"


@dataclass(frozen=True)
class ParsedTreasury:
    series: dict[str, tuple[Observation, ...]]  # security_desc -> ascending observations
    truncated: bool


def parse_response(text: str) -> ParsedTreasury:
    data = loads_decimal(text)
    if not isinstance(data, dict):
        raise ParseError("treasury response: expected an object")
    if "error" in data:
        raise ParseError("treasury API returned an error object", code="treasury_error")
    rows = data.get("data")
    meta = data.get("meta")
    if not isinstance(rows, list) or not isinstance(meta, dict):
        raise ParseError("treasury response: missing data/meta")
    types = meta.get("dataTypes")
    if not isinstance(types, dict) or types.get("avg_interest_rate_amt") != "PERCENTAGE":
        raise ParseError("treasury response: avg_interest_rate_amt is not declared PERCENTAGE")
    pages = meta.get("total-pages")
    truncated = isinstance(pages, int) and not isinstance(pages, bool) and pages > 1
    if not rows:
        raise ParseError("treasury response: no rows", code="no_data")
    grouped: dict[str, dict[str, Observation]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ParseError(f"row[{i}]: expected an object")
        date, kind, desc, rate = (row.get(f) for f in FIELDS)
        if not isinstance(date, str) or not _DATE.match(date):
            raise ParseError(f"row[{i}].record_date: expected YYYY-MM-DD")
        if kind != "Marketable":
            raise ParseError(f"row[{i}]: unexpected security_type_desc")
        if not isinstance(desc, str) or not desc or len(desc) > 80:
            raise ParseError(f"row[{i}].security_desc: expected text")
        if rate is None or (isinstance(rate, str) and rate.strip().lower() == "null"):
            continue  # Fiscal Data represents missing values as null / "null"
        grouped.setdefault(desc, {})[date] = Observation(
            date, to_decimal_string(rate, f"row[{i}].avg_interest_rate_amt")
        )
    if not grouped:
        raise ParseError("treasury response: all rates are empty", code="no_data")
    return ParsedTreasury(
        series={d: tuple(o for _, o in sorted(obs.items())) for d, obs in sorted(grouped.items())},
        truncated=truncated,
    )
