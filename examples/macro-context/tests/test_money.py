from decimal import Decimal

import pytest

from macro_context.errors import ParseError, ValidationError
from macro_context.money import assert_no_floats, loads_decimal, require_int, to_decimal_string


def test_decimal_strings_are_digit_exact():
    assert to_decimal_string(Decimal("2.65"), "v") == "2.65"
    assert to_decimal_string(3, "v") == "3"
    assert to_decimal_string(" 1.2838 ", "v") == "1.2838"
    assert to_decimal_string(Decimal("1E+3"), "v") == "1000"  # never exponent notation
    assert to_decimal_string("-0.5", "v") == "-0.5"


@pytest.mark.parametrize(
    "value", [1.5, True, None, [], "", "abc", "NaN", "Infinity", "1e999", Decimal("NaN"), "١٢", Decimal("1E+30")]
)
def test_bad_values_rejected(value):
    with pytest.raises(ParseError):
        to_decimal_string(value, "v")


def test_strings_can_be_refused():
    with pytest.raises(ParseError):
        to_decimal_string("1.5", "v", allow_str=False)


@pytest.mark.parametrize("text", ["NaN", "-Infinity", "{", "", "[" * 100000])
def test_loads_decimal_rejects(text):
    with pytest.raises(ParseError):
        loads_decimal(text)


def test_loads_decimal_floats_are_decimals_and_size_capped():
    assert loads_decimal("[0.1]")[0] == Decimal("0.1") and not isinstance(loads_decimal("[0.1]")[0], float)
    with pytest.raises(ParseError) as err:
        loads_decimal("[1,1,1,1]", max_chars=3)
    assert err.value.code == "response_too_large"
    with pytest.raises(ParseError):
        loads_decimal(b"[]")  # type: ignore[arg-type]


def test_require_int_and_assert_no_floats():
    assert require_int(3, "x") == 3
    for bad in (True, 1.0, "1", -1):
        with pytest.raises(ParseError):
            require_int(bad, "x")
    assert_no_floats({"a": ["1.5", 2]})
    for bad in ({"a": 0.5}, {"a": [Decimal("1")]}):
        with pytest.raises(ValidationError):
            assert_no_floats(bad)
