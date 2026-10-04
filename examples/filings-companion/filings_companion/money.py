"""Decimal handling for XBRL values: exact Decimal in, canonical decimal string out.

XBRL ``val`` numbers are parsed with ``json.loads(parse_float=Decimal)`` so a value such as
6.13 never becomes a binary float. Values are reported exactly as filed (no rounding, no
currency conversion); the unit travels with the value.
"""

from __future__ import annotations

from decimal import Decimal

from .errors import ParseError


def decimal_str(value: object, *, what: str) -> str:
    """Canonical plain-decimal string (no exponent) for an int or Decimal."""
    if isinstance(value, bool | float) or not isinstance(value, int | Decimal):
        raise ParseError(f"{what}: value is not a JSON number")
    dec = Decimal(value)
    if not dec.is_finite():
        raise ParseError(f"{what}: value is not finite")
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text
