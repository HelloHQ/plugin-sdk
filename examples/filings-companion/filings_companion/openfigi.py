"""OpenFIGI identifier mapping (keyless).

Documentation: https://www.openfigi.com/api/documentation
  POST https://api.openfigi.com/v3/mapping, body = JSON array of mapping jobs
  ({"idType": "ID_ISIN", "idValue": "...", optional "exchCode", "marketSecDes", ...}).
  Response = JSON array, one element per job, each with exactly one of "data" (list of
  instruments with figi, name, ticker, exchCode, compositeFIGI, shareClassFIGI,
  securityType, marketSector, securityDescription), "warning" (no match) or "error".
  Keyless limits: 25 requests/minute and 10 jobs per request (with a key: 25 per 6 s and 100).
  429 on excess; ratelimit-limit / ratelimit-remaining / ratelimit-reset headers.
This plugin never uses a key, so jobs are batched in groups of 10 and requests are limited to
25 per minute by the Fetcher.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import ParseError, ValidationError
from .transport import OPENFIGI_ORIGIN, Fetcher, loads_decimal

MAPPING_URL = f"https://{OPENFIGI_ORIGIN}/v3/mapping"
MAX_JOBS_PER_REQUEST = 10
_ID_TYPE = {"isin": "ID_ISIN", "cusip": "ID_CUSIP", "figi": "ID_BB_GLOBAL", "ticker": "TICKER"}
_OPTIONAL = (
    "name", "ticker", "exchCode", "compositeFIGI", "shareClassFIGI", "securityType",
    "marketSector", "securityDescription",
)  # fmt: skip


@dataclass(frozen=True)
class FigiMatch:
    figi: str
    name: str | None = None
    ticker: str | None = None
    exch_code: str | None = None
    composite_figi: str | None = None
    share_class_figi: str | None = None
    security_type: str | None = None
    market_sector: str | None = None


@dataclass(frozen=True)
class JobResult:
    matches: tuple[FigiMatch, ...] = field(default_factory=tuple)
    warning: str | None = None
    error: str | None = None


def build_job(identifier: Mapping[str, str]) -> dict[str, str]:
    """One mapping job for a validated {"type", "value"} identifier."""
    kind = identifier.get("type", "")
    if kind not in _ID_TYPE:
        raise ValidationError(f"OpenFIGI cannot map identifier type '{kind}'")
    job = {"idType": _ID_TYPE[kind], "idValue": identifier["value"]}
    if kind == "ticker":
        job["exchCode"] = "US"  # a bare ticker is ambiguous worldwide; stay on US listings
    return job


def batches(jobs: Sequence[Mapping[str, str]], size: int = MAX_JOBS_PER_REQUEST):
    if not 1 <= size <= MAX_JOBS_PER_REQUEST:
        raise ValidationError(f"keyless OpenFIGI allows at most {MAX_JOBS_PER_REQUEST} jobs")
    for i in range(0, len(jobs), size):
        yield list(jobs[i : i + size])


def parse_mapping_response(text: str, *, expected: int) -> list[JobResult]:
    doc = loads_decimal(text, what="OpenFIGI")
    if not isinstance(doc, list):
        raise ParseError("OpenFIGI: expected a JSON array")
    if len(doc) != expected:
        raise ParseError(f"OpenFIGI: expected {expected} results, got {len(doc)}")
    results: list[JobResult] = []
    for item in doc:
        if not isinstance(item, dict):
            raise ParseError("OpenFIGI: result is not an object")
        present = [k for k in ("data", "warning", "error") if k in item]
        if len(present) != 1:
            raise ParseError("OpenFIGI: result must have exactly one of data/warning/error")
        if "warning" in item:
            results.append(JobResult(warning=str(item["warning"])))
        elif "error" in item:
            results.append(JobResult(error=str(item["error"])))
        else:
            if not isinstance(item["data"], list):
                raise ParseError("OpenFIGI: 'data' is not a list")
            results.append(JobResult(matches=tuple(_match(d) for d in item["data"])))
    return results


def _match(d: Any) -> FigiMatch:
    if not isinstance(d, dict) or not isinstance(d.get("figi"), str):
        raise ParseError("OpenFIGI: instrument is missing 'figi'")
    opt = {k: (str(d[k]) if d.get(k) is not None else None) for k in _OPTIONAL}
    return FigiMatch(
        figi=d["figi"],
        name=opt["name"],
        ticker=opt["ticker"],
        exch_code=opt["exchCode"],
        composite_figi=opt["compositeFIGI"],
        share_class_figi=opt["shareClassFIGI"],
        security_type=opt["securityType"],
        market_sector=opt["marketSector"],
    )


def map_identifiers(fetcher: Fetcher, jobs: Sequence[Mapping[str, str]]) -> list[JobResult]:
    """Map jobs in batches of 10, one request per batch, in order."""
    out: list[JobResult] = []
    for batch in batches(jobs):
        resp = fetcher.request(MAPPING_URL, method="POST", body=json.dumps(batch))
        out.extend(parse_mapping_response(resp.body, expected=len(batch)))
    return out
