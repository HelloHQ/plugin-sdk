"""Singapore HDB resale flat transactions via the data.gov.sg dataset API.

Documented in the data.gov.sg developer guide ("Dataset APIs" > "Search and filter within
dataset": https://guide.data.gov.sg/developer-guide/dataset-apis/search-and-filter-within-dataset
and the dataset page for d_8b84c4ee58e3cfc0ece0d773c8ca6abc, "Resale flat prices based on
registration date from Jan-2017 onwards"). Rate limit: 4 requests / 10 s unauthenticated
(https://guide.data.gov.sg/developer-guide/api-overview/api-rate-limits).

Block, street, storey and flat model columns exist in the source and are deliberately never
read into the model: this plugin is aggregate-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from .errors import ParseError, ValidationError
from .estimate import Sale
from .money import to_minor
from .transport import SG_ORIGIN, Fetcher, loads_decimal, window_start

SG_DATASET_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
SG_ENDPOINT = f"https://{SG_ORIGIN}/api/action/datastore_search"
SG_PAGE_LIMIT = 100  # documented default and maximum page size
SG_MAX_PAGES = 10
SG_CURRENCY = "SGD"
FLAT_TYPES = frozenset(
    {"1 ROOM", "2 ROOM", "3 ROOM", "4 ROOM", "5 ROOM", "EXECUTIVE", "MULTI-GENERATION"}
)
SG_ATTRIBUTION = (
    'Contains information from "Resale flat prices based on registration date from '
    'Jan-2017 onwards" from data.gov.sg, made available under the Singapore Open Data '
    "Licence."
)
_TOWN_RE = re.compile(r"^[A-Z][A-Z '/-]{1,39}$")
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_REQUIRED_FIELDS = ("month", "town", "flat_type", "floor_area_sqm", "resale_price")


def sg_validate_params(raw: Mapping[str, Any]) -> dict[str, Any]:
    town = str(raw.get("town", "")).strip().upper()
    if not _TOWN_RE.match(town):
        raise ValidationError("town: expected an HDB town name such as 'BISHAN'")
    flat_type = str(raw.get("flat_type", "")).strip().upper()
    if flat_type not in FLAT_TYPES:
        raise ValidationError(f"flat_type: one of {sorted(FLAT_TYPES)}")
    months = raw.get("months", 12)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 60:
        raise ValidationError("months: integer between 1 and 60")
    return {"town": town, "flat_type": flat_type, "months": months}


def sg_build_url(params: Mapping[str, Any], *, offset: int) -> str:
    filters = json.dumps(
        {"town": params["town"], "flat_type": params["flat_type"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    query = urlencode(
        {
            "resource_id": SG_DATASET_ID,
            "limit": SG_PAGE_LIMIT,
            "offset": offset,
            "sort": "month desc",
            "filters": filters,
        }
    )
    return f"{SG_ENDPOINT}?{query}"


def sg_parse_page(text: str) -> tuple[list[Mapping[str, Any]], int | None]:
    """Return (records, total) from one datastore_search response."""
    doc = loads_decimal(text, what="data.gov.sg")
    if not isinstance(doc, dict) or doc.get("success") is not True:
        raise ParseError("data.gov.sg: 'success' is not true")
    result = doc.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("records"), list):
        raise ParseError("data.gov.sg: missing result.records")
    total = result.get("total")
    return result["records"], total if isinstance(total, int) else None


def sg_record_to_sale(record: Any, *, since: date) -> tuple[Sale | None, str | None]:
    """Convert one record. Returns (sale, None), (None, skip_reason) or raises ParseError.

    A missing field is an error (the response shape changed). A present-but-unusable value
    skips only that row, with a counted reason.
    """
    if not isinstance(record, dict):
        raise ParseError("data.gov.sg: record is not an object")
    for name in _REQUIRED_FIELDS:
        if name not in record:
            raise ParseError(f"data.gov.sg: record is missing '{name}'")
    match = _MONTH_RE.match(str(record["month"]))
    if not match:
        return None, "bad_month"
    sold_on = date(int(match.group(1)), int(match.group(2)), 1)
    if sold_on < since:
        return None, "outside_window"
    try:
        price = Decimal(str(record["resale_price"]).strip())
        if not price.is_finite() or price <= 0:
            return None, "bad_price"
        area = Decimal(str(record["floor_area_sqm"]).strip())
        area_val = area if area.is_finite() and area > 0 else None
    except InvalidOperation:
        return None, "bad_number"
    return Sale(sold_on=sold_on, price_minor=to_minor(price), area_sqm=area_val), None


def sg_collect(
    fetcher: Fetcher, params: Mapping[str, Any], *, as_of: date
) -> tuple[list[Sale], dict[str, int], list[str]]:
    """Fetch pages (newest first) until the window is covered. Returns (sales, skips, urls)."""
    since = window_start(as_of, params["months"])
    sales: list[Sale] = []
    skipped: dict[str, int] = {}
    urls: list[str] = []
    offset = 0
    for _ in range(SG_MAX_PAGES):
        url = sg_build_url(params, offset=offset)
        urls.append(url)
        records, total = sg_parse_page(fetcher.get(url, accept="application/json").body)
        window_done = False
        for rec in records:
            sale, reason = sg_record_to_sale(rec, since=since)
            if sale:
                sales.append(sale)
            elif reason == "outside_window":
                window_done = True  # sorted newest-first: everything after is older
            elif reason:
                skipped[reason] = skipped.get(reason, 0) + 1
        offset += SG_PAGE_LIMIT
        if window_done or len(records) < SG_PAGE_LIMIT or (total is not None and offset >= total):
            break
    return sales, skipped, urls
