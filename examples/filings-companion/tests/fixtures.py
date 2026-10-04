"""Hand-written fixtures built from DOCUMENTED shapes. No real personal data; companies are
fictional ("Testco Holdings", CIK 1234567).

* SEC endpoints (URL patterns, 10-digit CIK):
  https://www.sec.gov/search-filings/edgar-application-programming-interfaces
  companyconcept units entries (end, val, accn, fy, fp, form, filed, frame) and submissions
  filings.recent parallel arrays were checked against live responses; the companyfacts and
  company_tickers.json shapes are NOT verified from documentation (see sec.py).
* OpenFIGI mapping request/response: https://www.openfigi.com/api/documentation
"""

from __future__ import annotations

import json
from decimal import Decimal

CIK = "0001234567"


def tickers_json(*entries: tuple[int, str, str]) -> str:
    return json.dumps(
        {
            str(i): {"cik_str": cik, "ticker": ticker, "title": title}
            for i, (cik, ticker, title) in enumerate(entries)
        }
    )


def submissions_json(filings, *, cik: int | str = 1234567, name="Testco Holdings", extra=None):
    """``filings`` = [(accession, form, filed, report_date, primary_document), ...]."""
    recent = {
        "accessionNumber": [f[0] for f in filings],
        "form": [f[1] for f in filings],
        "filingDate": [f[2] for f in filings],
        "reportDate": [f[3] for f in filings],
        "primaryDocument": [f[4] for f in filings],
    }
    doc = {
        "cik": str(cik),
        "name": name,
        "tickers": ["TSTC"],
        "filings": {"recent": recent, "files": []},
    }
    doc.update(extra or {})
    return json.dumps(doc)


FILINGS = [
    ("0001234567-26-000010", "8-K", "2026-09-01", "2026-08-30", "tstc-8k.htm"),
    ("0001234567-26-000007", "10-Q", "2026-08-05", "2026-06-30", "tstc-10q.htm"),
    ("0001234567-25-000003", "10-K", "2025-11-10", "2025-09-30", "tstc-10k.htm"),
    ("0001234567-25-000002", "4", "2025-10-01", "", "form4.xml"),
]


def fact(end, val, *, form="10-K", fp="FY", fy=2025, filed="2025-11-10", accn=None, start=None):
    e = {"end": end, "val": val, "accn": accn or "0001234567-25-000003", "fy": fy, "fp": fp}
    e.update(form=form, filed=filed)
    if start:
        e["start"] = start
    return e


def facts_json(concepts: dict, *, cik=1234567, name="Testco Holdings") -> str:
    """``concepts`` = {("us-gaap", "Revenues"): {"USD": [fact, ...]}, ...}"""
    facts: dict = {}
    for (taxonomy, tag), units in concepts.items():
        facts.setdefault(taxonomy, {})[tag] = {"label": tag, "description": "d", "units": units}
    # Decimal values must round-trip as JSON numbers (floats in the wire format)
    return json.dumps({"cik": cik, "entityName": name, "facts": facts}, default=_dec)


def _dec(o):
    if isinstance(o, Decimal):
        return float(o)  # test-only: simulates a JSON float on the wire
    raise TypeError


def concept_json(units: dict, *, tag="Revenues", taxonomy="us-gaap") -> str:
    return json.dumps(
        {
            "cik": 1234567,
            "taxonomy": taxonomy,
            "tag": tag,
            "label": tag,
            "description": "d",
            "entityName": "Testco Holdings",
            "units": units,
        }
    )


def figi_item(figi="BBG000000001", ticker="TSTC", exch="US", name="TESTCO HOLDINGS INC"):
    return {
        "figi": figi,
        "name": name,
        "ticker": ticker,
        "exchCode": exch,
        "compositeFIGI": "BBG000000001",
        "shareClassFIGI": "BBG000000002",
        "securityType": "Common Stock",
        "marketSector": "Equity",
        "securityDescription": ticker,
    }


def figi_response(*results) -> str:
    """Each result: list of figi_item (data), or a str -> warning, or ("error", msg)."""
    out = []
    for r in results:
        if isinstance(r, str):
            out.append({"warning": r})
        elif isinstance(r, tuple):
            out.append({"error": r[1]})
        else:
            out.append({"data": r})
    return json.dumps(out)
