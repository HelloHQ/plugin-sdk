import json

import pytest

from fixtures import WB_ERROR, wb_message, wb_row
from macro_context import worldbank as wb
from macro_context.errors import ParseError, ValidationError


def parse(text, country="SGP", indicator="FP.CPI.TOTL.ZG"):
    return wb.parse_response(text, country=country, indicator=indicator)


def test_valid_sorted_ascending_nulls_skipped_digits_exact():
    rows = [wb_row("2025", 0.902998495002676), wb_row("2024", 2.38951123595017), wb_row("2023", None)]
    parsed = parse(wb_message(rows))
    assert [(o.period, o.value) for o in parsed.observations] == [
        ("2024", "2.38951123595017"),
        ("2025", "0.902998495002676"),
    ]
    assert parsed.title == "Inflation, consumer prices (annual %)" and parsed.last_updated == "2026-07-13"
    assert parsed.truncated is False


def test_integer_and_zero_values():
    assert parse(wb_message([wb_row("2025", 0), wb_row("2024", 3)])).observations[0].value == "3"


def test_all_null_and_null_body_are_no_data():
    for text in (
        wb_message([wb_row("2025", None)]),
        json.dumps([{"page": 0, "pages": 0, "total": 0}, None]),
        wb_message([]),
    ):
        with pytest.raises(ParseError) as err:
            parse(text)
        assert err.value.code == "no_data"


def test_error_body_raises_worldbank_error():
    with pytest.raises(wb.WorldBankError) as err:
        parse(WB_ERROR)
    assert "not valid" in err.value.message


@pytest.mark.parametrize(
    "row",
    [
        wb_row("2025", 1, indicator="OTHER.IND"),
        wb_row("2025", 1, iso3="USA"),
        wb_row("2025", 1, iso3=None),
        wb_row("2020Q1", 1),
        wb_row(None, 1),
        wb_row("2025", "1.5"),
        wb_row("2025", 1.5e999),
        {"indicator": {"id": "FP.CPI.TOTL.ZG", "value": "t"}, "countryiso3code": "SGP", "date": "2025"},  # no value key
        "nope",
    ],
)
def test_bad_rows_raise(row):
    text = wb_message([row]) if not isinstance(row, str) else json.dumps([{"pages": 1}, [row]])
    with pytest.raises(ParseError):
        parse(text)


@pytest.mark.parametrize("text", ["", "{}", "[]", "[1,2]", '[{"pages":1}]', '[{"pages":1},{}]', "null"])
def test_garbage_raises(text):
    with pytest.raises(ParseError):
        parse(text)


def test_truncation_flag_from_paging_metadata():
    assert parse(wb_message([wb_row("2025", 1)], pages=3)).truncated is True


def test_huge_input():
    rows = [wb_row(f"{1000 + i}", i) for i in range(3000)]
    assert len(parse(wb_message(rows)).observations) == 3000


def test_country_validation_and_request():
    assert wb.validate_country(" sgp ") == "SGP"
    for bad in ("", "SG", "SGPX", "S1P", "../..", None, 5):
        with pytest.raises(ValidationError):
            wb.validate_country(bad)
    assert (
        wb.url_for("SGP", "FP.CPI.TOTL.ZG", 5)
        == "https://api.worldbank.org/v2/country/SGP/indicator/FP.CPI.TOTL.ZG?format=json&mrv=5&per_page=5"
    )
    with pytest.raises(ValueError):
        wb.request_path("SGP", "bad indicator", 5)
    with pytest.raises(ValueError):
        wb.request_path("SGP", "FP.CPI.TOTL.ZG", 0)
