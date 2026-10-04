"""England and Wales: HM Land Registry Price Paid Data.

Two inputs, both reduced to price + date only (the address columns are discarded at parse
time; this plugin is aggregate-only):

* the Linked Data API (JSON), fetched from landregistry.data.gov.uk. Documentation lives at
  https://landregistry.data.gov.uk/app/doc/ppd/ but could not be read when this was written;
  the response shape below was OBSERVED from a live response and is parsed defensively.
* the published CSV files (https://www.gov.uk/guidance/about-the-price-paid-data: 16 columns,
  no header row by default, "with headers" variants exist). Yearly files are 115-230 MB, far
  above the host's 8 MiB response cap, so CSV is supported only as person-provided text.

Attribution under the Open Government Licence v3.0 is mandatory and is included in output.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import urlencode

from .errors import InputTooLarge, ParseError, ValidationError
from .estimate import Sale
from .money import to_minor
from .transport import UK_ORIGIN, Fetcher, loads_decimal, window_start

UK_ENDPOINT = f"https://{UK_ORIGIN}/data/ppi/transaction-record.json"
UK_PAGE_SIZE = 200  # the API caps itemsPerPage at 200 (observed)
UK_MAX_PAGES = 10
UK_CURRENCY = "GBP"
UK_MAX_CSV_CHARS = 64 * 1024 * 1024
_TYPE_SEGMENT = {"D": "detached", "S": "semi-detached", "T": "terraced", "F": "flat-maisonette"}
_TYPE_URI = "http://landregistry.data.gov.uk/def/common/"
_STANDARD = "standardPricePaidTransaction"
_NAME_RE = re.compile(r"^[A-Z][A-Z &'.-]{1,59}$")
_DATE_RE = re.compile(r"^[A-Za-z]{3}, (\d{1,2}) ([A-Za-z]{3}) (\d{4})$")
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}
_CSV_COLUMNS = 15  # monthly files add a 16th "record status" column


def uk_attribution(year: int) -> str:
    return (
        f"Contains HM Land Registry data © Crown copyright and database right {year}. "
        "This data is licensed under the Open Government Licence v3.0."
    )


def uk_validate_params(raw: Mapping[str, Any]) -> dict[str, Any]:
    district = str(raw.get("district", "")).strip().upper()
    if not _NAME_RE.match(district):
        raise ValidationError("district: expected a local authority district such as 'LEEDS'")
    town = str(raw.get("town", "")).strip().upper()
    if town and not _NAME_RE.match(town):
        raise ValidationError("town: invalid")
    ptype = str(raw.get("property_type", "")).strip().upper()
    if ptype and ptype not in _TYPE_SEGMENT:
        raise ValidationError("property_type: one of D, S, T, F (or omit)")
    months = raw.get("months", 12)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 60:
        raise ValidationError("months: integer between 1 and 60")
    return {"district": district, "town": town, "property_type": ptype, "months": months}


def uk_build_url(params: Mapping[str, Any], *, since: date, page: int) -> str:
    query: dict[str, Any] = {"propertyAddress.district": params["district"]}
    if params["town"]:
        query["propertyAddress.town"] = params["town"]
    if params["property_type"]:
        query["propertyType"] = _TYPE_URI + _TYPE_SEGMENT[params["property_type"]]
    query.update(
        {
            "min-transactionDate": since.isoformat(),
            "_sort": "-transactionDate",
            "_pageSize": UK_PAGE_SIZE,
            "_page": page,
        }
    )
    return f"{UK_ENDPOINT}?{urlencode(query)}"


def _parse_api_date(value: Any) -> date | None:
    match = _DATE_RE.match(str(value))
    if not match or match.group(2) not in _MONTHS:
        return None
    try:
        return date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))
    except ValueError:
        return None


def parse_api_page(text: str, *, since: date) -> tuple[list[Sale], dict[str, int], bool]:
    """Return (sales, skipped, has_next) from one Linked Data API page."""
    doc = loads_decimal(text, what="Land Registry")
    result = doc.get("result") if isinstance(doc, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise ParseError("Land Registry: missing result.items")
    sales: list[Sale] = []
    skipped: dict[str, int] = {}
    for item in result["items"]:
        if not isinstance(item, dict):
            raise ParseError("Land Registry: item is not an object")
        for name in ("pricePaid", "transactionDate", "propertyType", "transactionCategory"):
            if name not in item:
                raise ParseError(f"Land Registry: item is missing '{name}'")
        category = str(_about(item["transactionCategory"])).rsplit("/", 1)[-1]
        status = str(_about(item.get("recordStatus", {}))).rsplit("/", 1)[-1]
        sold_on = _parse_api_date(item["transactionDate"])
        price = item["pricePaid"]
        reason = None
        if category != _STANDARD:
            reason = "non_standard_category"
        elif status == "delete":
            reason = "deleted_record"
        elif sold_on is None:
            reason = "bad_date"
        elif sold_on < since:
            reason = "outside_window"
        elif isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            reason = "bad_price"
        if reason or sold_on is None:
            skipped[reason or "bad_date"] = skipped.get(reason or "bad_date", 0) + 1
            continue
        sales.append(Sale(sold_on=sold_on, price_minor=to_minor(price)))
    return sales, skipped, isinstance(result.get("next"), str)


def _about(node: Any) -> str:
    return str(node.get("_about", "")) if isinstance(node, dict) else ""


def uk_collect(
    fetcher: Fetcher, params: Mapping[str, Any], *, as_of: date
) -> tuple[list[Sale], dict[str, int], list[str]]:
    since = window_start(as_of, params["months"])
    sales: list[Sale] = []
    skipped: dict[str, int] = {}
    urls: list[str] = []
    for page in range(UK_MAX_PAGES):
        url = uk_build_url(params, since=since, page=page)
        urls.append(url)
        got, skips, has_next = parse_api_page(
            fetcher.get(url, accept="application/json").body, since=since
        )
        sales.extend(got)
        for key, count in skips.items():
            skipped[key] = skipped.get(key, 0) + count
        if not has_next:
            break
    return sales, skipped, urls


def parse_ppd_csv(
    text: str,
    *,
    district: str | None = None,
    town: str | None = None,
    property_type: str | None = None,
    since: date,
    max_chars: int = UK_MAX_CSV_CHARS,
) -> tuple[list[Sale], dict[str, int]]:
    """Parse Price Paid CSV text (with or without a header row), keeping price and date only."""
    if len(text) > max_chars:
        raise InputTooLarge(f"CSV larger than {max_chars} characters")
    district_u = (district or "").upper()
    town_u = (town or "").upper()
    ptype_u = (property_type or "").upper()
    sales: list[Sale] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for row_no, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row:
            continue
        if row_no == 1 and row[0].strip().lower().startswith("transaction unique"):
            continue  # a "with headers" variant
        if len(row) not in (_CSV_COLUMNS, _CSV_COLUMNS + 1):
            raise ParseError(f"Price Paid CSV: row {row_no} has {len(row)} columns, expected 15/16")
        price_s, date_s, ptype, cat = row[1], row[2], row[4], row[14]
        row_town, row_district = row[11].strip().upper(), row[12].strip().upper()
        if (
            (district_u and row_district != district_u)
            or (town_u and row_town != town_u)
            or (ptype_u and ptype.strip().upper() != ptype_u)
        ):
            continue  # not the area/type asked for: not counted as skipped
        if len(row) > _CSV_COLUMNS and row[15].strip().upper() == "D":
            skip("deleted_record")
        elif cat.strip().upper() != "A":
            skip("non_standard_category")
        else:
            sold_on = _parse_csv_date(date_s)
            if sold_on is None:
                skip("bad_date")
            elif sold_on < since:
                skip("outside_window")
            elif not price_s.strip().isdigit() or int(price_s) <= 0:
                skip("bad_price")
            else:
                sales.append(Sale(sold_on=sold_on, price_minor=to_minor(int(price_s))))
    return sales, skipped


def _parse_csv_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
