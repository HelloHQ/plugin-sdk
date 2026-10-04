"""France: DVF (Demandes de valeurs foncieres), geolocated CSV published by Etalab.

Sources: https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres (licence: Licence
Ouverte 2.0) and the geolocated dataset https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees
Per-commune CSVs live under https://files.data.gouv.fr/geo-dvf/latest/csv/<year>/communes/<dep>/<insee>.csv
(path observed from the live listing; the column names are those of the geolocated dataset
page). files.data.gouv.fr answers 302 to an object-storage origin, handled in transport.py.

AGGREGATE ONLY. The dataset page states that data contains personal data and that reusers
must prevent indirect re-identification of individuals. Therefore:
  * address, parcel, coordinates and mutation id columns are used only transiently to group
    rows and are never stored in the model or output;
  * no individual sale (min, max or list) is ever output;
  * the minimum-sample rule applies.
The exact legal limits remain UNVERIFIED (see README): treat this parser as aggregate-only.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import HttpError, InputTooLarge, ParseError, SourceUnavailable, ValidationError
from .estimate import Sale
from .money import to_minor
from .transport import FR_ORIGIN, Fetcher, window_start

FR_CURRENCY = "EUR"
FR_BASE = f"https://{FR_ORIGIN}/geo-dvf/latest/csv"
FR_MAX_CSV_CHARS = 64 * 1024 * 1024
FR_ATTRIBUTION = (
    "Source: Direction generale des finances publiques (DGFiP), Demandes de valeurs "
    "foncieres, geolocated by Etalab (data.gouv.fr). Licence Ouverte / Open Licence 2.0."
)
_INSEE_RE = re.compile(r"^(\d{5}|2[AB]\d{3})$")
_DWELLING = {"apartment": "Appartement", "house": "Maison"}
_REQUIRED_COLUMNS = (
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "type_local",
    "surface_reelle_bati",
)


def fr_validate_params(raw: Mapping[str, Any], *, as_of: date) -> dict[str, Any]:
    commune = str(raw.get("commune", "")).strip().upper()
    if not _INSEE_RE.match(commune):
        raise ValidationError("commune: expected a 5-character INSEE code such as '75101'")
    ptype = str(raw.get("property_type", "")).strip().lower()
    if ptype not in _DWELLING:
        raise ValidationError("property_type: 'apartment' or 'house'")
    years = raw.get("years", [as_of.year - 2, as_of.year - 1])
    if (
        not isinstance(years, list)
        or not 1 <= len(years) <= 5
        or any(isinstance(y, bool) or not isinstance(y, int) for y in years)
        or any(y < 2014 or y > as_of.year for y in years)
    ):
        raise ValidationError("years: list of 1-5 years (2014 or later)")
    months = raw.get("months", 24)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 60:
        raise ValidationError("months: integer between 1 and 60")
    return {
        "commune": commune,
        "property_type": ptype,
        "years": sorted(set(years)),
        "months": months,
    }


def fr_build_url(commune: str, year: int) -> str:
    dep = commune[:3] if commune.startswith("97") else commune[:2]
    return f"{FR_BASE}/{year}/communes/{dep}/{commune}.csv"


def _decimal(value: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        dec = Decimal(text)
    except InvalidOperation:
        return None
    return dec if dec.is_finite() else None


def parse_dvf_csv(
    text: str,
    *,
    property_type: str,
    since: date,
    max_chars: int = FR_MAX_CSV_CHARS,
) -> tuple[list[Sale], dict[str, int]]:
    """Reduce DVF rows to one Sale per qualifying single-dwelling sale (price, date, area)."""
    if len(text) > max_chars:
        raise InputTooLarge(f"CSV larger than {max_chars} characters")
    wanted = _DWELLING[property_type]
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    missing = [c for c in _REQUIRED_COLUMNS if c not in fields]
    if missing:
        raise ParseError(f"DVF CSV: missing columns {missing}")
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in reader:
        groups[row["id_mutation"]].append(row)

    sales: list[Sale] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for rows in groups.values():
        first = rows[0]
        if first["nature_mutation"].strip() != "Vente":
            skip("not_a_plain_sale")
            continue
        types = [r["type_local"].strip() for r in rows]
        mains = [t for t in types if t in ("Appartement", "Maison")]
        if any(t.startswith("Local") for t in types) or len(mains) != 1:
            skip("not_a_single_dwelling")
            continue
        if mains[0] != wanted:
            skip("other_property_type")
            continue
        values = {r["valeur_fonciere"].strip() for r in rows}
        if len(values) != 1:
            skip("ambiguous_price")
            continue
        price = _decimal(next(iter(values)))
        main_row = next(r for r in rows if r["type_local"].strip() == wanted)
        area = _decimal(main_row["surface_reelle_bati"])
        try:
            sold_on = date.fromisoformat(first["date_mutation"].strip())
        except ValueError:
            skip("bad_date")
            continue
        if sold_on < since:
            skip("outside_window")
        elif price is None or price <= 0:
            skip("bad_price")
        elif area is None or area <= 0:
            skip("no_floor_area")
        else:
            sales.append(Sale(sold_on=sold_on, price_minor=to_minor(price), area_sqm=area))
    return sales, skipped


def fr_collect(
    fetcher: Fetcher, params: Mapping[str, Any], *, as_of: date
) -> tuple[list[Sale], dict[str, int], list[str]]:
    since = window_start(as_of, params["months"])
    sales: list[Sale] = []
    skipped: dict[str, int] = {}
    urls: list[str] = []
    unavailable = 0
    for year in params["years"]:
        url = fr_build_url(params["commune"], year)
        urls.append(url)
        try:
            body = fetcher.get(url, accept="text/csv").body
        except HttpError as exc:
            if exc.status != 404:
                raise
            unavailable += 1
            continue
        got, skips = parse_dvf_csv(body, property_type=params["property_type"], since=since)
        sales.extend(got)
        for key, count in skips.items():
            skipped[key] = skipped.get(key, 0) + count
    if unavailable == len(params["years"]):
        raise SourceUnavailable("no DVF file is published for that commune and those years")
    if unavailable:
        skipped["year_file_unavailable"] = unavailable
    return sales, skipped, urls
