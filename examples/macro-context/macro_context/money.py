"""Exact numeric handling for macro series. No binary floats anywhere.

Rules (also in the README):

* Vendor JSON is parsed with ``parse_float=Decimal``; a number such as
  ``2.65`` keeps exactly the digits the source published.
* Values are re-emitted as plain decimal STRINGS, digit-for-digit as
  published. Nothing is rounded, scaled or converted by this plugin.
* ``float`` is rejected wherever a value is expected (``assert_no_floats``).
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from macro_context.errors import ParseError, ValidationError

MAX_ABS_VALUE = Decimal(10) ** 18  # sanity bound; the largest series here is a debt level in USD


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
    except RecursionError as exc:
        raise ParseError("JSON nested too deeply") from exc


def require_int(value: Any, name: str, *, minimum: int | None = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"{name}: expected an integer")
    if minimum is not None and value < minimum:
        raise ParseError(f"{name}: must be >= {minimum}")
    return value


def plain(value: Decimal) -> str:
    """Decimal -> string without exponent notation."""
    return format(value, "f")


def to_decimal_string(value: Any, name: str, *, allow_str: bool = True) -> str:
    """A published value (JSON number or numeric string) as an exact decimal string.

    Accepts ``int``, ``Decimal`` and (unless ``allow_str`` is false) ``str``; rejects ``float``, ``bool``,
    NaN/Infinity and absurd magnitudes.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ParseError(f"{name}: expected a decimal number")
    if isinstance(value, str):
        if not allow_str:
            raise ParseError(f"{name}: expected a JSON number")
        text = value.strip()
        if not text or not text.isascii():
            raise ParseError(f"{name}: expected a decimal number")
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ParseError(f"{name}: expected a decimal number") from None
    elif isinstance(value, (int, Decimal)):
        number = Decimal(value)
    else:
        raise ParseError(f"{name}: expected a decimal number")
    if not number.is_finite() or abs(number) > MAX_ABS_VALUE:
        raise ParseError(f"{name}: not a plausible finite number")
    return plain(number)


def assert_no_floats(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        raise ValidationError(f"float at {path}: values must be strings", code="float_value")
    if isinstance(obj, dict):
        for key, val in obj.items():
            assert_no_floats(val, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            assert_no_floats(val, f"{path}[{i}]")
    elif isinstance(obj, Decimal):
        raise ValidationError(f"Decimal at {path}: serialise values as strings", code="float_value")
