"""Hand-written fixtures built from DOCUMENTED shapes. No real personal data: every address
is synthetic ("TEST STREET", "EXAMPLE AVE"). Shapes and where they are documented:

* data.gov.sg datastore_search response and the HDB dataset columns:
  https://guide.data.gov.sg/developer-guide/dataset-apis/search-and-filter-within-dataset
  https://data.gov.sg/datasets?query=resale+flat+prices&resultId=d_8b84c4ee58e3cfc0ece0d773c8ca6abc
* HM Land Registry Price Paid CSV (column order, codes):
  https://www.gov.uk/guidance/about-the-price-paid-data
  Linked Data API JSON: shape OBSERVED from a live response (documentation page unreadable
  at time of writing): https://landregistry.data.gov.uk/app/doc/ppd/
* France DVF geolocated CSV column names:
  https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees
* Ireland PPR CSV: columns UNVERIFIED (third-party descriptions); see ie_ppr.py.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable


def prices(n: int, start: int = 400_000, step: int = 5_000) -> list[int]:
    return [start + i * step for i in range(n)]


# --- Singapore HDB (data.gov.sg) --------------------------------------------------------
def sg_record(month: str, price: int | str, area: str = "93", **over: str) -> dict:
    rec = {
        "_id": 1,
        "month": month,
        "town": "BISHAN",
        "flat_type": "4 ROOM",
        "block": "000",
        "street_name": "EXAMPLE AVE 1",
        "storey_range": "10 TO 12",
        "floor_area_sqm": area,
        "flat_model": "Model A",
        "lease_commence_date": "1990",
        "remaining_lease": "63 years 00 months",
        "resale_price": str(price),
    }
    rec.update(over)
    return rec


def sg_page(records: Iterable[dict], total: int | None = None) -> str:
    recs = list(records)
    return json.dumps(
        {
            "success": True,
            "result": {
                "resource_id": "d_8b84c4ee58e3cfc0ece0d773c8ca6abc",
                "fields": [{"type": "text", "id": "month"}],
                "records": recs,
                "limit": 100,
                "total": len(recs) if total is None else total,
                "_links": {"start": "/api/action/datastore_search?x=1"},
            },
        }
    )


# --- UK Price Paid ------------------------------------------------------------------------
def uk_item(
    price: int,
    date_s: str = "Mon, 24 Aug 2026",
    ptype: str = "terraced",
    category: str = "standardPricePaidTransaction",
    status: str = "add",
) -> dict:
    return {
        "_about": "http://landregistry.data.gov.uk/data/ppi/transaction/TEST/current",
        "pricePaid": price,
        "transactionDate": date_s,
        "newBuild": False,
        "propertyType": {"_about": f"http://landregistry.data.gov.uk/def/common/{ptype}"},
        "transactionCategory": {"_about": f"http://landregistry.data.gov.uk/def/ppi/{category}"},
        "recordStatus": {"_about": f"http://landregistry.data.gov.uk/def/ppi/{status}"},
        "propertyAddress": {
            "paon": "1",
            "street": "TEST STREET",
            "town": "TESTVILLE",
            "district": "TESTDISTRICT",
            "postcode": "ZZ1 1ZZ",
        },
    }


def uk_page(items: Iterable[dict], has_next: bool = False) -> str:
    result: dict = {"items": list(items), "itemsPerPage": 200, "page": 0}
    if has_next:
        result["next"] = "https://landregistry.data.gov.uk/data/ppi/transaction-record.json?_page=1"
    return json.dumps({"format": "linked-data-api", "version": "0.2", "result": result})


def uk_csv_row(
    price: int,
    date_s: str = "2026-08-24 00:00",
    ptype: str = "T",
    district: str = "TESTDISTRICT",
    town: str = "TESTVILLE",
    category: str = "A",
    status: str | None = "A",
) -> str:
    cols = [
        "{TEST-ID}", str(price), date_s, "ZZ1 1ZZ", ptype, "N", "F", "1", "", "TEST STREET",
        "", town, district, "TESTSHIRE", category,
    ]  # fmt: skip
    if status is not None:
        cols.append(status)
    return ",".join(f'"{c}"' for c in cols)


UK_CSV_HEADER = (
    "Transaction unique identifier,Price,Date of Transfer,Postcode,Property Type,Old/New,"
    "Duration,PAON,SAON,Street,Locality,Town/City,District,County,PPD Category Type,"
    "Record Status"
)

# --- France DVF ---------------------------------------------------------------------------
DVF_HEADER = (
    "id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,"
    "adresse_numero,adresse_suffixe,adresse_nom_voie,adresse_code_voie,code_postal,"
    "code_commune,nom_commune,code_departement,ancien_code_commune,ancien_nom_commune,"
    "id_parcelle,ancien_id_parcelle,numero_volume,lot1_numero,lot1_surface_carrez,"
    "lot2_numero,lot2_surface_carrez,lot3_numero,lot3_surface_carrez,lot4_numero,"
    "lot4_surface_carrez,lot5_numero,lot5_surface_carrez,nombre_lots,code_type_local,"
    "type_local,surface_reelle_bati,nombre_pieces_principales,code_nature_culture,"
    "nature_culture,code_nature_culture_speciale,nature_culture_speciale,surface_terrain,"
    "longitude,latitude"
)
_DVF_COLS = DVF_HEADER.split(",")


def dvf_row(
    mid: str,
    date_s: str,
    value: str,
    type_local: str,
    surface: str,
    nature: str = "Vente",
) -> str:
    row = dict.fromkeys(_DVF_COLS, "")
    row.update(
        id_mutation=mid,
        date_mutation=date_s,
        numero_disposition="000001",
        nature_mutation=nature,
        valeur_fonciere=value,
        adresse_nom_voie="RUE DE TEST",
        code_commune="99999",
        id_parcelle="99999000AA0001",
        type_local=type_local,
        surface_reelle_bati=surface,
        longitude="0.0",
        latitude="0.0",
    )
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow([row[c] for c in _DVF_COLS])
    return buf.getvalue()


# --- Ireland PPR --------------------------------------------------------------------------
IE_HEADER = (
    "Date of Sale (dd/mm/yyyy),Address,Postal Code,County,Price (€),"
    "Not Full Market Price,VAT Exclusive,Description of Property,Property Size Description"
)


def ie_row(
    date_s: str,
    price: str,
    county: str = "Dublin",
    nfmp: str = "No",
    desc: str = "Second-Hand Dwelling house /Apartment",
) -> str:
    return f'{date_s},"1 Test Road, Testtown",,{county},"{price}",{nfmp},No,"{desc}",'
