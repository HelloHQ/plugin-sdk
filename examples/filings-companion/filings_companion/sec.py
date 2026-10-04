"""SEC EDGAR: submissions, XBRL company facts / concepts, and the ticker-to-CIK file.

Documented endpoints (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
  https://data.sec.gov/submissions/CIK##########.json
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  https://data.sec.gov/api/xbrl/companyconcept/CIK##########/<taxonomy>/<tag>.json
(the CIK is the entity's 10-digit number with leading zeros). The ticker file is documented at
https://www.sec.gov/file/company-tickers and served from https://www.sec.gov/files/company_tickers.json.

Shape provenance (see README): the field names of companyconcept units entries (end, val,
accn, fy, fp, form, filed, frame) and of submissions filings.recent parallel arrays
(accessionNumber, filingDate, reportDate, form, primaryDocument, ...) were checked against
live responses; the SEC page does not spell them all out. The companyfacts shape
(facts -> taxonomy -> tag -> {label, units -> unit -> [entries]}) and the company_tickers.json
shape ({"0": {"cik_str", "ticker", "title"}}) are NOT verified from documentation: they are
parsed defensively, and a missing field is an explicit ParseError.

Facts are returned exactly as filed. Nothing is derived, converted or adjusted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from .errors import ParseError
from .identifiers import cik10
from .money import decimal_str
from .transport import SEC_DATA_ORIGIN, SEC_WWW_ORIGIN, loads_decimal

TICKERS_URL = f"https://{SEC_WWW_ORIGIN}/files/company_tickers.json"
DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "20-F", "40-F", "6-K", "DEF 14A")
ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F", "40-F/A")
_DOC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,200}$")
_ACCN_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")

# (output key, label, [(taxonomy, tag, unit), ...] tried in order)
KEY_CONCEPTS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "revenue",
        "Revenue",
        (
            ("us-gaap", "Revenues", "USD"),
            ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
            ("us-gaap", "SalesRevenueNet", "USD"),
        ),
    ),
    ("net_income", "Net income (loss)", (("us-gaap", "NetIncomeLoss", "USD"),)),
    ("assets", "Total assets", (("us-gaap", "Assets", "USD"),)),
    ("liabilities", "Total liabilities", (("us-gaap", "Liabilities", "USD"),)),
    ("equity", "Stockholders' equity", (("us-gaap", "StockholdersEquity", "USD"),)),
    (
        "eps_diluted",
        "Diluted earnings per share",
        (("us-gaap", "EarningsPerShareDiluted", "USD/shares"),),
    ),
    (
        "shares_outstanding",
        "Shares outstanding (cover page)",
        (("dei", "EntityCommonStockSharesOutstanding", "shares"),),
    ),
)


def submissions_url(cik: int | str) -> str:
    return f"https://{SEC_DATA_ORIGIN}/submissions/CIK{cik10(cik)}.json"


def companyfacts_url(cik: int | str) -> str:
    return f"https://{SEC_DATA_ORIGIN}/api/xbrl/companyfacts/CIK{cik10(cik)}.json"


def companyconcept_url(cik: int | str, taxonomy: str, tag: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9\-]{1,30}", taxonomy) or not _TAG_RE.match(tag):
        raise ParseError("concept: invalid taxonomy or tag")
    return (
        f"https://{SEC_DATA_ORIGIN}/api/xbrl/companyconcept/CIK{cik10(cik)}/{taxonomy}/{tag}.json"
    )


# ------------------------------------------------------------------ ticker file
def parse_company_tickers(text: str) -> dict[str, dict[str, Any]]:
    """Return {TICKER: {"cik": "0000320193", "title": ...}} from company_tickers.json."""
    doc = loads_decimal(text, what="SEC company tickers")
    if not isinstance(doc, dict):
        raise ParseError("SEC company tickers: expected an object")
    out: dict[str, dict[str, Any]] = {}
    for entry in doc.values():
        if not isinstance(entry, dict):
            raise ParseError("SEC company tickers: entry is not an object")
        for name in ("cik_str", "ticker", "title"):
            if name not in entry:
                raise ParseError(f"SEC company tickers: entry is missing '{name}'")
        try:
            cik = cik10(str(entry["cik_str"]))
        except Exception as exc:  # noqa: BLE001 - re-raised as a parse error
            raise ParseError("SEC company tickers: bad cik_str") from exc
        out.setdefault(str(entry["ticker"]).upper(), {"cik": cik, "title": str(entry["title"])})
    return out


def lookup_ticker(table: Mapping[str, Mapping[str, Any]], ticker: str) -> Mapping[str, Any] | None:
    """Exact match, then the EDGAR share-class spelling (BRK.B -> BRK-B)."""
    t = ticker.upper()
    return table.get(t) or table.get(t.replace(".", "-"))


# ------------------------------------------------------------------ submissions
@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filed: str
    report_date: str | None
    primary_document: str | None
    url: str | None  # display link only; never fetched by this plugin

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "form": self.form,
            "filed": self.filed,
            "report_date": self.report_date,
            "primary_document": self.primary_document,
            "url": self.url,
        }


def _iso(value: Any, what: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ParseError(f"{what}: not an ISO date") from exc


def parse_submissions(
    text: str, *, forms: tuple[str, ...] | None = DEFAULT_FORMS, limit: int = 5
) -> dict[str, Any]:
    """Return {"cik", "name", "tickers", "filings": [Filing...]} (recent filings only)."""
    doc = loads_decimal(text, what="SEC submissions")
    if not isinstance(doc, dict):
        raise ParseError("SEC submissions: expected an object")
    for name in ("cik", "name", "filings"):
        if name not in doc:
            raise ParseError(f"SEC submissions: missing '{name}'")
    recent = doc["filings"].get("recent") if isinstance(doc["filings"], dict) else None
    if not isinstance(recent, dict):
        raise ParseError("SEC submissions: missing filings.recent")
    cols: dict[str, list[Any]] = {}
    for name in ("accessionNumber", "filingDate", "form"):
        if not isinstance(recent.get(name), list):
            raise ParseError(f"SEC submissions: filings.recent.{name} is missing")
        cols[name] = recent[name]
    n = len(cols["accessionNumber"])
    if any(len(v) != n for v in cols.values()):
        raise ParseError("SEC submissions: filings.recent arrays differ in length")
    optional: dict[str, list[Any] | None] = {}
    for name in ("reportDate", "primaryDocument"):
        col = recent.get(name)
        if col is not None and (not isinstance(col, list) or len(col) != n):
            raise ParseError(f"SEC submissions: filings.recent.{name} is malformed")
        optional[name] = col
    cik = cik10(str(doc["cik"]))
    wanted = set(forms) if forms else None
    filings: list[Filing] = []
    for i in range(n):
        form = str(cols["form"][i])
        if wanted is not None and form not in wanted:
            continue
        accn = str(cols["accessionNumber"][i])
        if not _ACCN_RE.match(accn):
            raise ParseError("SEC submissions: malformed accession number")
        rd = optional["reportDate"][i] if optional["reportDate"] else ""
        pd_ = optional["primaryDocument"][i] if optional["primaryDocument"] else ""
        pdoc = str(pd_) if pd_ and _DOC_RE.match(str(pd_)) else None
        filings.append(
            Filing(
                accession=accn,
                form=form,
                filed=_iso(cols["filingDate"][i], "filingDate"),
                report_date=_iso(rd, "reportDate") if rd else None,
                primary_document=pdoc,
                url=(
                    f"https://{SEC_WWW_ORIGIN}/Archives/edgar/data/{int(cik)}/"
                    f"{accn.replace('-', '')}/{pdoc}"
                    if pdoc
                    else None
                ),
            )
        )
        if len(filings) >= limit:
            break
    tickers = doc.get("tickers") if isinstance(doc.get("tickers"), list) else []
    return {
        "cik": cik,
        "name": str(doc["name"]),
        "tickers": [str(t) for t in tickers],
        "filings": filings,
    }


# ------------------------------------------------------------------ XBRL facts
def _annual_entries(entries: Any, *, what: str) -> list[Mapping[str, Any]]:
    if not isinstance(entries, list):
        raise ParseError(f"{what}: units entry is not a list")
    out = []
    for e in entries:
        if not isinstance(e, dict):
            raise ParseError(f"{what}: fact entry is not an object")
        for name in ("end", "val", "accn", "form", "filed"):
            if name not in e:
                raise ParseError(f"{what}: fact entry is missing '{name}'")
        if e.get("fp") == "FY" and e["form"] in ANNUAL_FORMS:
            out.append(e)
        # entries for other periods/forms are intentionally ignored, not guessed at
    return out


def _fact_dict(
    key: str, label: str, taxonomy: str, tag: str, unit: str, e: Mapping[str, Any]
) -> dict[str, Any]:
    what = f"{taxonomy}:{tag}"
    fy = e.get("fy")
    return {
        "key": key,
        "label": label,
        "concept": what,
        "value": decimal_str(e["val"], what=what),
        "unit": unit,
        "period_start": _iso(e["start"], what) if e.get("start") else None,
        "period_end": _iso(e["end"], what),
        "fiscal_year": fy if isinstance(fy, int) and not isinstance(fy, bool) else None,
        "form": str(e["form"]),
        "filed": _iso(e["filed"], what),
        "accession": str(e["accn"]),
    }


def latest_annual(entries: Any, *, what: str) -> Mapping[str, Any] | None:
    """The most recent annual (FY, annual-form) entry: latest period end, then latest filing."""
    annual = _annual_entries(entries, what=what)
    if not annual:
        return None
    return max(annual, key=lambda e: (_iso(e["end"], what), _iso(e["filed"], what)))


def parse_company_facts(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Return (company, facts, unavailable) from a companyfacts response.

    ``unavailable`` lists the key concepts with no usable annual value, explicitly.
    """
    doc = loads_decimal(text, what="SEC company facts")
    if not isinstance(doc, dict):
        raise ParseError("SEC company facts: expected an object")
    for name in ("cik", "entityName", "facts"):
        if name not in doc:
            raise ParseError(f"SEC company facts: missing '{name}'")
    if not isinstance(doc["facts"], dict):
        raise ParseError("SEC company facts: 'facts' is not an object")
    facts: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for key, label, candidates in KEY_CONCEPTS:
        chosen = None
        for taxonomy, tag, unit in candidates:
            node = doc["facts"].get(taxonomy, {})
            tag_node = node.get(tag) if isinstance(node, dict) else None
            if tag_node is None:
                continue
            if not isinstance(tag_node, dict) or not isinstance(tag_node.get("units"), dict):
                raise ParseError(f"SEC company facts: {taxonomy}:{tag} has no units object")
            units = tag_node["units"]
            entry = latest_annual(units[unit], what=f"{taxonomy}:{tag}") if unit in units else None
            if entry is not None:
                chosen = _fact_dict(key, label, taxonomy, tag, unit, entry)
                break
        if chosen:
            facts.append(chosen)
        else:
            unavailable.append(key)
    company = {"cik": cik10(str(doc["cik"])), "name": str(doc["entityName"])}
    return company, facts, unavailable


def parse_company_concept(
    text: str, *, key: str, label: str, taxonomy: str, tag: str, unit: str
) -> dict[str, Any] | None:
    """Parse one companyconcept response into the same fact shape (or None if no annual value)."""
    doc = loads_decimal(text, what="SEC company concept")
    if not isinstance(doc, dict) or not isinstance(doc.get("units"), dict):
        raise ParseError("SEC company concept: missing units")
    if unit not in doc["units"]:
        return None
    entry = latest_annual(doc["units"][unit], what=f"{taxonomy}:{tag}")
    return _fact_dict(key, label, taxonomy, tag, unit, entry) if entry else None
