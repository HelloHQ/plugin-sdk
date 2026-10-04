"""Orchestration: contact, identifier resolution, filings and key facts, with provenance.

INFORMATION ONLY. Shows what companies reported in public SEC filings. Not advice.
Flow for each identifier the person supplies (the plugin cannot see their holdings):
  cik        -> used directly
  ticker     -> SEC company_tickers.json
  isin/cusip/figi -> OpenFIGI (US listings only) -> ticker -> SEC company_tickers.json
then data.sec.gov submissions (recent filings) and XBRL company facts (latest annual values).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contact import get_contact, set_contact
from .errors import PluginCoreError, RateLimited, ResponseTooLarge, ValidationError
from .hostapi import Host
from .identifiers import validate_identifiers
from .openfigi import FigiMatch, build_job, map_identifiers
from .sec import (
    DEFAULT_FORMS,
    KEY_CONCEPTS,
    TICKERS_URL,
    companyconcept_url,
    companyfacts_url,
    lookup_ticker,
    parse_company_concept,
    parse_company_facts,
    parse_company_tickers,
    parse_submissions,
    submissions_url,
)
from .transport import Fetcher

LABEL = (
    "Information only. Figures are as reported by each company in its SEC filings (XBRL) and "
    "are not verified, normalised or adjusted. Nothing here is investment advice or a "
    "recommendation."
)
SOURCES = (
    "U.S. Securities and Exchange Commission, EDGAR (data.sec.gov, www.sec.gov). "
    "Identifier mapping, where used: OpenFIGI (openfigi.com)."
)
MAX_FILINGS = 10


def configure_contact(host: Host, request: Mapping[str, Any]) -> dict[str, Any]:
    contact = set_contact(host, request.get("contact"))
    return {"configured": True, "contact": contact}


def contact_status(host: Host) -> dict[str, Any]:
    try:
        return {"configured": True, "contact": get_contact(host)}
    except PluginCoreError as exc:
        return {"configured": False, "reason": exc.message}


def _prov(origin: str, url: str, fetched_at: str) -> dict[str, str]:
    return {"origin": origin, "url": url, "fetched_at": fetched_at}


def lookup_holdings(host: Host, request: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve identifiers and return filings and key facts, one entry per identifier."""
    identifiers = validate_identifiers(request.get("identifiers"))
    forms = tuple(request.get("forms") or DEFAULT_FORMS)
    limit = request.get("filings_limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FILINGS:
        raise ValidationError(f"filings_limit: integer 1..{MAX_FILINGS}")
    include_facts = request.get("include_facts", True)
    if not isinstance(include_facts, bool):
        raise ValidationError("include_facts: true or false")
    if not all(isinstance(f, str) and 1 <= len(f) <= 12 for f in forms):
        raise ValidationError("forms: list of short form names such as '10-K'")

    get_contact(host)  # refuse early, before any request, when no contact is configured
    fetcher = Fetcher(host)
    now = lambda: host.now().strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    results: list[dict[str, Any]] = [
        {"identifier": ident, "status": "pending", "sources": []} for ident in identifiers
    ]
    aborted: dict[str, str] | None = None

    try:
        _resolve(fetcher, results, now)
        for res in results:
            if res["status"] == "resolved":
                _fill_company(fetcher, res, forms, limit, include_facts, now)
    except RateLimited as exc:
        aborted = {"code": exc.code, "message": exc.message}
        if exc.retry_after_s is not None:
            aborted["retry_after_s"] = str(exc.retry_after_s)
        for res in results:
            if res["status"] in ("pending", "resolved") and "company" not in res:
                res["status"] = "not_attempted"

    return {
        "label": LABEL,
        "source": SOURCES,
        "fetched_at": now(),
        "requests_made": fetcher.requests_made,
        "aborted": aborted,
        "results": results,
    }


def _resolve(fetcher: Fetcher, results: list[dict[str, Any]], now) -> None:
    """Fill ``cik`` / ``status`` for each result, using as few requests as possible."""
    needs_figi = [r for r in results if r["identifier"]["type"] in ("isin", "cusip", "figi")]
    needs_table = [r for r in results if r["identifier"]["type"] != "cik"]
    for r in results:
        if r["identifier"]["type"] == "cik":
            r.update(status="resolved", cik=r["identifier"]["value"], via="cik")

    figi_results: dict[int, Any] = {}
    if needs_figi:
        jobs = [build_job(r["identifier"]) for r in needs_figi]
        mapped = map_identifiers(fetcher, jobs)
        stamp = now()
        for r, job, m in zip(needs_figi, jobs, mapped, strict=True):
            figi_results[id(r)] = m
            r["sources"].append(
                _prov("api.openfigi.com", "https://api.openfigi.com/v3/mapping", stamp)
                | {"identifier_used": job}
            )

    table = None
    if needs_table:
        resp = fetcher.request(TICKERS_URL)
        table = parse_company_tickers(resp.body)
        stamp = now()
        for r in needs_table:
            r["sources"].append(_prov("www.sec.gov", TICKERS_URL, stamp))

    for r in needs_table:
        ident = r["identifier"]
        if ident["type"] == "ticker":
            ticker = ident["value"]
            r["via"] = "sec_ticker_file"
        else:
            m = figi_results[id(r)]
            if m.error or m.warning:
                ticker, why = None, m.error or "OpenFIGI found no match for that identifier"
            else:
                ticker, why = _pick_us_ticker(m.matches)
            if ticker is None:
                r.update(status="unresolved", reason=why)
                continue
            r["via"] = "openfigi+sec_ticker_file"
            r["figi"] = _first_figi(m.matches)
        hit = lookup_ticker(table, ticker)
        if hit is None:
            r.update(status="unresolved", ticker=ticker, reason="not found in the SEC ticker file")
        else:
            r.update(status="resolved", cik=hit["cik"], ticker=ticker, sec_title=hit["title"])


def _first_figi(matches: Sequence[FigiMatch]) -> str | None:
    return matches[0].composite_figi or matches[0].figi if matches else None


def _pick_us_ticker(matches: Sequence[FigiMatch]) -> tuple[str | None, str]:
    us = [m for m in matches if m.exch_code == "US" and m.ticker]
    if not us:
        return None, "no US-listed instrument found; this plugin covers SEC filers only"
    tickers = sorted({m.ticker for m in us if m.ticker})
    if len(tickers) > 1:
        return None, f"ambiguous: several US tickers match ({', '.join(tickers)})"
    return tickers[0], ""


def _fill_company(
    fetcher: Fetcher,
    res: dict[str, Any],
    forms: tuple[str, ...],
    limit: int,
    include_facts: bool,
    now,
) -> None:
    cik = res["cik"]
    try:
        url = submissions_url(cik)
        sub = parse_submissions(fetcher.request(url).body, forms=forms, limit=limit)
        res["sources"].append(_prov("data.sec.gov", url, now()) | {"identifier_used": {"cik": cik}})
        res["company"] = {"cik": sub["cik"], "name": sub["name"], "tickers": sub["tickers"]}
        res["filings"] = [f.to_dict() for f in sub["filings"]]
        if include_facts:
            facts, unavailable = _facts(fetcher, cik, res, now)
            res["facts"], res["facts_unavailable"] = facts, unavailable
        res["status"] = "ok"
    except RateLimited:
        raise
    except PluginCoreError as exc:
        res.update(status="error", error={"code": exc.code, "message": exc.message})


def _facts(fetcher: Fetcher, cik: str, res: dict[str, Any], now):
    url = companyfacts_url(cik)
    try:
        _, facts, unavailable = parse_company_facts(fetcher.request(url).body)
        res["sources"].append(_prov("data.sec.gov", url, now()) | {"identifier_used": {"cik": cik}})
        return facts, unavailable
    except ResponseTooLarge:
        # Very large filers exceed the host's response cap: fall back to one small request
        # per concept (all documented companyconcept endpoints).
        facts, unavailable = [], []
        for key, label, candidates in KEY_CONCEPTS:
            found = None
            for taxonomy, tag, unit in candidates:
                curl = companyconcept_url(cik, taxonomy, tag)
                try:
                    body = fetcher.request(curl).body
                except PluginCoreError as exc:
                    if getattr(exc, "status", None) == 404:
                        continue
                    raise
                res["sources"].append(
                    _prov("data.sec.gov", curl, now()) | {"identifier_used": {"cik": cik}}
                )
                found = parse_company_concept(
                    body, key=key, label=label, taxonomy=taxonomy, tag=tag, unit=unit
                )
                if found:
                    break
            if found:
                facts.append(found)
            else:
                unavailable.append(key)
        return facts, unavailable
