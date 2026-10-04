from decimal import Decimal

import pytest

from wallet_tracker.errors import ParseError, ValidationError
from wallet_tracker.money import (
    assert_no_floats,
    fiat_value,
    format_units,
    loads_decimal,
    parse_price,
    require_int,
    require_uint_string,
)


@pytest.mark.parametrize(
    ("amount", "decimals", "expected"),
    [
        (0, 8, "0.00000000"),
        (1, 8, "0.00000001"),
        (51_230_000, 8, "0.51230000"),
        (2_100_000_000_000_000, 8, "21000000.00000000"),
        (1_000_000_000, 9, "1.000000000"),
        (123, 0, "123"),
        (5, 18, "0.000000000000000005"),
        (2**64 - 1, 6, "18446744073709.551615"),
    ],
)
def test_format_units_is_exact(amount, decimals, expected):
    assert format_units(amount, decimals) == expected


@pytest.mark.parametrize(("amount", "decimals"), [(-1, 8), (1, 19), (1, -1), (True, 8), (1.5, 8)])
def test_format_units_rejects_bad_input(amount, decimals):
    with pytest.raises(ValidationError):
        format_units(amount, decimals)


def test_fiat_value_rounds_half_even_once_to_minor_units():
    # 0.5123 BTC * 60000.55 = 30738.281765 -> 2 dp
    assert fiat_value(51_230_000, 8, Decimal("60000.55"), "USD") == "30738.28"
    # Half-even on exact halves, zero-decimal currency.
    assert fiat_value(5, 1, Decimal(1), "JPY") == "0"  # 0.5 -> 0
    assert fiat_value(15, 1, Decimal(1), "JPY") == "2"  # 1.5 -> 2
    assert fiat_value(25, 1, Decimal(1), "JPY") == "2"  # 2.5 -> 2
    # 2 dp half case: 0.125 -> 0.12 (even), 0.135 -> 0.14
    assert fiat_value(125, 3, Decimal(1), "USD") == "0.12"
    assert fiat_value(135, 3, Decimal(1), "USD") == "0.14"


def test_fiat_value_huge_and_zero_quantities_stay_exact():
    assert fiat_value(0, 8, Decimal("60000.55"), "USD") == "0.00"
    big = fiat_value(2_100_000_000_000_000, 8, Decimal("1000000"), "USD")
    assert big == "21000000000000.00"


def test_fiat_value_rejects_unknown_currency():
    with pytest.raises(ValidationError) as err:
        fiat_value(1, 8, Decimal(1), "XXX")
    assert err.value.code == "unsupported_currency"


def test_loads_decimal_never_produces_floats():
    data = loads_decimal('{"a": 0.1, "b": [1.25, 3]}')
    assert data["a"] == Decimal("0.1") and not isinstance(data["a"], float)
    assert isinstance(data["b"][0], Decimal) and isinstance(data["b"][1], int)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "{", "", '{"a": nan}'])
def test_loads_decimal_rejects_garbage(text):
    with pytest.raises(ParseError):
        loads_decimal(text)


def test_loads_decimal_size_cap_and_depth():
    with pytest.raises(ParseError) as err:
        loads_decimal("[" + "0," * 50 + "0]", max_chars=10)
    assert err.value.code == "response_too_large"
    with pytest.raises(ParseError):
        loads_decimal("[" * 100000 + "]" * 100000)


@pytest.mark.parametrize("value", [True, "5", 1.5, Decimal("1.5"), -1, None])
def test_require_int_rejects(value):
    with pytest.raises(ParseError):
        require_int(value, "x")


@pytest.mark.parametrize("value", ["", "-1", "1.5", "1e3", "٣", " 1", 5, None, "9" * 41])
def test_require_uint_string_rejects(value):
    with pytest.raises(ParseError):
        require_uint_string(value, "x")


def test_parse_price():
    assert parse_price(60000, "p") == Decimal(60000)
    assert parse_price(Decimal("0.5"), "p") == Decimal("0.5")
    for bad in (0, -1, True, 1.5, "10", None, Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ParseError):
            parse_price(bad, "p")


def test_assert_no_floats():
    assert_no_floats({"a": ["1.5", 2, {"b": "0.1"}]})
    for bad in ({"a": 0.1}, {"a": [1, {"b": 2.0}]}, {"a": Decimal("1")}):
        with pytest.raises(ValidationError) as err:
            assert_no_floats(bad)
        assert err.value.code == "float_money"
