"""Identifier validation: CIK, ticker, ISIN (Luhn), CUSIP (check digit), FIGI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import ValidationError

ID_TYPES = ("cik", "ticker", "isin", "cusip", "figi")
MAX_IDENTIFIERS = 10
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CUSIP_RE = re.compile(r"^[A-Z0-9*@#]{8}[0-9]$")
_FIGI_RE = re.compile(r"^BBG[A-Z0-9]{9}$")


def cik10(cik: int | str) -> str:
    """Zero-pad a CIK to the 10 digits the SEC endpoints require."""
    text = str(cik).strip()
    if isinstance(cik, bool) or not text.isdigit() or not 1 <= len(text) <= 10:
        raise ValidationError("cik: 1 to 10 digits")
    if int(text) == 0:
        raise ValidationError("cik: must not be zero")
    return text.zfill(10)


def isin_is_valid(isin: str) -> bool:
    if not _ISIN_RE.match(isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


def cusip_is_valid(cusip: str) -> bool:
    if not _CUSIP_RE.match(cusip):
        return False
    total = 0
    for i, ch in enumerate(cusip[:8]):
        v = int(ch) if ch.isdigit() else {"*": 36, "@": 37, "#": 38}.get(ch, ord(ch) - 55)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(cusip[8])


def validate_identifier(raw: Mapping[str, Any]) -> dict[str, str]:
    """Return {"type", "value"} normalised, or raise ValidationError."""
    if not isinstance(raw, Mapping):
        raise ValidationError("identifier: expected {type, value}")
    kind = str(raw.get("type", "")).strip().lower()
    value = str(raw.get("value", "")).strip()
    if kind not in ID_TYPES:
        raise ValidationError(f"identifier type: one of {list(ID_TYPES)}")
    if kind == "cik":
        return {"type": kind, "value": cik10(value)}
    value = value.upper()
    ok = {
        "ticker": lambda v: bool(_TICKER_RE.match(v)),
        "isin": isin_is_valid,
        "cusip": cusip_is_valid,
        "figi": lambda v: bool(_FIGI_RE.match(v)),
    }[kind](value)
    if not ok:
        raise ValidationError(f"{kind}: not a valid {kind.upper()}")
    return {"type": kind, "value": value}


def validate_identifiers(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_IDENTIFIERS:
        raise ValidationError(f"identifiers: a list of 1 to {MAX_IDENTIFIERS} items")
    seen: set[tuple[str, str]] = set()
    out = []
    for item in raw:
        ident = validate_identifier(item)
        key = (ident["type"], ident["value"])
        if key not in seen:
            seen.add(key)
            out.append(ident)
    return out
