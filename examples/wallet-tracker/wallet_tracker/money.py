"""Exact money handling. No binary floats anywhere.

Rules (also stated in the README):

* Chain amounts are integers in the smallest unit (satoshis, lamports, raw
  token units) and are only ever turned into decimal STRINGS by integer
  arithmetic (``format_units``), with a fixed scale equal to the asset's
  decimals. Quantities are therefore exact and never rounded.
* Vendor JSON is parsed with ``parse_float=Decimal`` so a number like ``0.1``
  never becomes a binary float. ``float`` values are rejected outright where
  an amount is expected.
* A fiat valuation is ``quantity * price`` computed exactly in ``Decimal`` and
  rounded ONCE, to the currency's ISO 4217 minor-unit exponent, with
  ``ROUND_HALF_EVEN`` (banker's rounding). Rounding happens nowhere else.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

from wallet_tracker.errors import ParseError, ValidationError

# ISO 4217 minor-unit exponents for the currencies mempool.space quotes.
CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "CHF": 2,
    "AUD": 2,
    "JPY": 0,
}

MAX_PRICE = Decimal(10) ** 15  # sanity bound; BTC in JPY is ~1e7
MAX_QUANTITY_SCALE = 18  # scale limit assumed by the proposed-quantity design


def loads_decimal(text: str, *, max_chars: int = 8 * 1024 * 1024) -> Any:
    """``json.loads`` with floats as ``Decimal`` and no NaN/Infinity."""
    if not isinstance(text, str):
        raise ParseError("response body is not text")
    if len(text) > max_chars:
        raise ParseError("response body too large", code="response_too_large")

    def _reject(token: str) -> Any:
        raise ParseError(f"non-finite number {token!r} in JSON")

    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc.msg}") from exc
    except RecursionError as exc:  # pathological nesting
        raise ParseError("JSON nested too deeply") from exc


def require_int(value: Any, name: str, *, minimum: int | None = 0) -> int:
    """A JSON integer (not bool, not float/Decimal with a fraction)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"{name}: expected an integer")
    if minimum is not None and value < minimum:
        raise ParseError(f"{name}: must be >= {minimum}")
    return value


def require_uint_string(value: Any, name: str) -> int:
    """A base-10 digit string such as Solana's ``tokenAmount.amount``."""
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 40:
        raise ParseError(f"{name}: expected a digit string")
    return int(value)


def format_units(amount: int, decimals: int) -> str:
    """Integer minor units -> fixed-scale decimal string, e.g. (51230000, 8) -> '0.51230000'."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValidationError("amount must be a non-negative integer of minor units")
    if not 0 <= decimals <= MAX_QUANTITY_SCALE:
        raise ValidationError(f"decimals must be 0..{MAX_QUANTITY_SCALE}")
    digits = str(amount)
    if decimals == 0:
        return digits
    digits = digits.rjust(decimals + 1, "0")
    return f"{digits[:-decimals]}.{digits[-decimals:]}"


def plain(value: Decimal) -> str:
    """Decimal -> string without exponent notation."""
    return format(value, "f")


def parse_price(value: Any, name: str) -> Decimal:
    """A strictly positive price from vendor JSON (int or Decimal, never float)."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ParseError(f"{name}: expected a decimal number")
    try:
        number = Decimal(value) if isinstance(value, (int, Decimal)) else None
    except InvalidOperation:  # pragma: no cover - Decimal(int/Decimal) cannot fail
        number = None
    if number is None or not number.is_finite() or number <= 0 or number > MAX_PRICE:
        raise ParseError(f"{name}: expected a positive finite number within a plausible range")
    return number


def fiat_value(units: int, decimals: int, price: Decimal, currency: str) -> str:
    """quantity (``units`` of ``10**-decimals``) * price, rounded once to minor units."""
    if currency not in CURRENCY_EXPONENTS:
        raise ValidationError(f"unsupported currency {currency!r}", code="unsupported_currency")
    exponent = CURRENCY_EXPONENTS[currency]
    with localcontext() as ctx:
        ctx.prec = 80
        exact = Decimal(units).scaleb(-decimals) * price
        quantum = Decimal(1).scaleb(-exponent)
        return plain(exact.quantize(quantum, rounding=ROUND_HALF_EVEN))


def assert_no_floats(obj: Any, path: str = "$") -> None:
    """Raise if any ``float`` appears anywhere in a JSON-bound structure."""
    if isinstance(obj, float):
        raise ValidationError(f"float at {path}: money must be strings or integers", code="float_money")
    if isinstance(obj, dict):
        for key, val in obj.items():
            assert_no_floats(val, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            assert_no_floats(val, f"{path}[{i}]")
    elif isinstance(obj, Decimal):
        raise ValidationError(f"Decimal at {path}: serialise amounts as strings", code="float_money")
