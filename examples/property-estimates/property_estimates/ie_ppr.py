"""Ireland: Residential Property Price Register (PSRA), person-provided CSV only.

The register is a bulk download with no API: https://www.propertypriceregister.ie/website/npsra/pprweb.nsf/PPRDownloads?OpenForm
is a web form (County + Year required, Month optional) submitted by POST to a Lotus Domino
application. No documented API or stable file URL exists, so this plugin does NOT fetch it;
the person downloads the CSV themselves and supplies the text.

Re-use terms (https://www.psr.ie/re-use-of-public-sector-information/): free re-use in any
format provided the source and PSRA copyright are acknowledged, the information is
reproduced accurately and not used misleadingly, and not used principally to advertise a
product or service. Those conditions are reflected in the attribution below and the
information-only label. The terms page says nothing on automated download; hence no fetch.

CSV column names are UNVERIFIED against an official specification (third-party descriptions
list: Date of Sale (dd/mm/yyyy), Address, Postal Code, County, Price, Not Full Market Price,
VAT Exclusive, Description of Property, Property Size Description). Columns are therefore
located by name; a missing required column is an explicit error. The Address column is never
read into the model.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .errors import InputTooLarge, ParseError, ValidationError
from .estimate import Sale
from .money import to_minor

IE_CURRENCY = "EUR"
IE_MAX_CSV_CHARS = 64 * 1024 * 1024
IE_ATTRIBUTION = (
    "Source: Property Services Regulatory Authority (PSRA), Residential Property Price "
    "Register. Copyright PSRA. Reproduced accurately; not for advertising a product or service."
)
_PRICE_RE = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?$")
_COUNTIES = frozenset(
    c.lower()
    for c in [
        "Carlow",
        "Cavan",
        "Clare",
        "Cork",
        "Donegal",
        "Dublin",
        "Galway",
        "Kerry",
        "Kildare",
        "Kilkenny",
        "Laois",
        "Leitrim",
        "Limerick",
        "Longford",
        "Louth",
        "Mayo",
        "Meath",
        "Monaghan",
        "Offaly",
        "Roscommon",
        "Sligo",
        "Tipperary",
        "Waterford",
        "Westmeath",
        "Wexford",
        "Wicklow",
    ]
)


def decode_csv_bytes(data: bytes) -> str:
    """Decode a downloaded file: UTF-8 (with or without BOM), else Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def ie_validate_params(raw: Mapping[str, Any]) -> dict[str, Any]:
    county = str(raw.get("county", "")).strip().lower()
    if county not in _COUNTIES:
        raise ValidationError("county: one of the 26 Republic of Ireland counties")
    desc = str(raw.get("dwelling", "")).strip().lower()
    if desc not in ("", "new", "second_hand"):
        raise ValidationError("dwelling: 'new', 'second_hand' or omit")
    months = raw.get("months", 12)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 60:
        raise ValidationError("months: integer between 1 and 60")
    csv_text = raw.get("csv_text")
    if not isinstance(csv_text, str) or not csv_text.strip():
        raise ValidationError("csv_text: provide the text of a CSV you downloaded from the PPR")
    return {"county": county, "dwelling": desc, "months": months}


def _find(headers: list[str], *prefixes: str) -> int | None:
    for i, h in enumerate(headers):
        if any(h.startswith(p) for p in prefixes):
            return i
    return None


def _parse_price(text: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d.,]", "", text)  # drops the euro sign and mojibake variants
    if not _PRICE_RE.match(cleaned):
        return None
    return Decimal(cleaned.replace(",", ""))


def parse_ppr_csv(
    text: str,
    *,
    county: str,
    since: date,
    dwelling: str = "",
    max_chars: int = IE_MAX_CSV_CHARS,
) -> tuple[list[Sale], dict[str, int]]:
    if len(text) > max_chars:
        raise InputTooLarge(f"CSV larger than {max_chars} characters")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header_row = next(reader)
    except StopIteration:
        raise ParseError("PPR CSV: empty input") from None
    headers = [h.strip().lower() for h in header_row]
    i_date = _find(headers, "date of sale")
    i_county = _find(headers, "county")
    i_price = _find(headers, "price")
    i_nfmp = _find(headers, "not full market price")
    i_desc = _find(headers, "description of property")
    missing = [
        name
        for name, idx in (("Date of Sale", i_date), ("County", i_county), ("Price", i_price))
        if idx is None
    ]
    if missing:
        raise ParseError(f"PPR CSV: missing required columns {missing}")
    assert i_date is not None and i_county is not None and i_price is not None  # for type checkers

    sales: list[Sale] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    need = max(i_date, i_county, i_price)
    for row in reader:
        if not row:
            continue
        if len(row) <= need:
            skip("short_row")
            continue
        if row[i_county].strip().lower() != county:
            continue
        if i_nfmp is not None and i_nfmp < len(row) and row[i_nfmp].strip().lower() == "yes":
            skip("not_full_market_price")
            continue
        if dwelling and i_desc is not None and i_desc < len(row):
            is_new = row[i_desc].strip().lower().startswith("new")
            if is_new != (dwelling == "new"):
                continue
        try:
            sold_on = datetime.strptime(row[i_date].strip(), "%d/%m/%Y").date()
        except ValueError:
            skip("bad_date")
            continue
        price = _parse_price(row[i_price])
        if sold_on < since:
            skip("outside_window")
        elif price is None or price <= 0:
            skip("bad_price")
        else:
            sales.append(Sale(sold_on=sold_on, price_minor=to_minor(price)))
    return sales, skipped
