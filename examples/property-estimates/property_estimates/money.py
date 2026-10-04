"""Money helpers: decimal strings and integer minor units only, never binary floats.

Rounding rule used everywhere in this plugin: round-half-even (banker's rounding) to the
currency's minor unit (2 decimal places for SGD, GBP and EUR), applied once, at the end.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction

from .errors import ValidationError

MINOR_EXPONENT = 2  # SGD, GBP, EUR all have 2 minor-unit digits
_DECIMAL_RE = re.compile(r"^-?\d{1,30}(\.\d{1,18})?$")


def parse_decimal(value: object, *, field: str = "value") -> Decimal:
    """Parse a decimal string or integer. Floats, bools, NaN, exponents are rejected."""
    if isinstance(value, bool | float):
        raise ValidationError(f"{field}: binary floats are not accepted", code="float_money")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError(f"{field}: not a finite number")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_RE.match(text):
            raise ValidationError(f"{field}: not a plain decimal string")
        return Decimal(text)
    raise ValidationError(f"{field}: unsupported type")


def to_minor(amount: Decimal | Fraction | int) -> int:
    """Convert a major-unit amount to integer minor units, rounding half-even."""
    if isinstance(amount, Fraction):
        scaled = amount * (10**MINOR_EXPONENT)
        return round(scaled)  # Fraction.__round__ is round-half-even
    dec = Decimal(amount) if isinstance(amount, int) else amount
    quant = dec.scaleb(MINOR_EXPONENT).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return int(quant)


def minor_to_str(minor: int) -> str:
    """Integer minor units to a canonical decimal string, for example 41250000 -> '412500.00'."""
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise ValidationError("minor units must be an int")
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 10**MINOR_EXPONENT)
    return f"{sign}{whole}.{frac:0{MINOR_EXPONENT}d}"


def round_for_proposal(minor: int) -> int:
    """Coarsen a median before proposing it, so a proposal never claims false precision.

    Values of 100,000 major units or more round to the nearest 1,000; smaller values to the
    nearest 100. Round-half-even on the quotient.
    """
    major_100k = 100_000 * 10**MINOR_EXPONENT
    step = (1_000 if minor >= major_100k else 100) * 10**MINOR_EXPONENT
    return round(Fraction(minor, step)) * step
