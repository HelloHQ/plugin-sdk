import json

import pytest

from fixtures import treasury_message, treasury_row
from macro_context import treasury
from macro_context.errors import ParseError


def test_valid_grouped_sorted_exact():
    rows = [
        treasury_row("2026-08-31", "Treasury Bills", "3.788"),
        treasury_row("2026-07-31", "Treasury Bills", "3.8"),
        treasury_row("2026-08-31", "Treasury Notes", "3.345"),
    ]
    parsed = treasury.parse_response(treasury_message(rows))
    assert list(parsed.series) == ["Treasury Bills", "Treasury Notes"]
    assert [(o.period, o.value) for o in parsed.series["Treasury Bills"]] == [
        ("2026-07-31", "3.8"),
        ("2026-08-31", "3.788"),
    ]
    assert parsed.truncated is False


def test_null_string_and_none_rates_are_skipped():
    rows = [
        treasury_row("2026-08-31", "Treasury Bills", "null"),
        treasury_row("2026-07-31", "Treasury Bills", "3.1"),
        treasury_row("2026-06-30", "Treasury Bills", None),
    ]
    assert [o.period for o in treasury.parse_response(treasury_message(rows)).series["Treasury Bills"]] == [
        "2026-07-31"
    ]


def test_all_null_and_empty_are_no_data():
    for rows in ([], [treasury_row("2026-08-31", "Treasury Bills", "null")]):
        with pytest.raises(ParseError) as err:
            treasury.parse_response(treasury_message(rows))
        assert err.value.code == "no_data"


def test_unit_must_be_declared_percentage():
    with pytest.raises(ParseError):
        treasury.parse_response(
            treasury_message([treasury_row("2026-08-31", "Treasury Bills", "3")], data_type="CURRENCY")
        )


def test_truncated_when_more_pages():
    assert (
        treasury.parse_response(
            treasury_message([treasury_row("2026-08-31", "Treasury Bills", "3")], pages=2)
        ).truncated
        is True
    )


@pytest.mark.parametrize(
    "row",
    [
        treasury_row("08/31/2026", "Treasury Bills", "3"),
        treasury_row("2026-08-31", "Treasury Bills", "3", kind="Non-marketable"),
        treasury_row("2026-08-31", "", "3"),
        treasury_row("2026-08-31", "Treasury Bills", "abc"),
        treasury_row("2026-08-31", "Treasury Bills", "NaN"),
        treasury_row("2026-08-31", "Treasury Bills", "1e999"),
        "nope",
    ],
)
def test_bad_rows_raise(row):
    with pytest.raises(ParseError):
        treasury.parse_response(treasury_message([row]))


@pytest.mark.parametrize(
    "text", ["", "[]", "{}", '{"data": []}', '{"error":"Invalid Query Param","message":"x"}', "null"]
)
def test_garbage_and_error_objects_raise(text):
    with pytest.raises(ParseError):
        treasury.parse_response(text)


def test_huge_input():
    rows = [treasury_row(f"{2000 + i // 12}-{i % 12 + 1:02d}-28", f"Security {i % 9}", "1.5") for i in range(5000)]
    assert len(treasury.parse_response(treasury_message(rows)).series) == 9


def test_request_encoding_and_validation():
    url = treasury.url_for("2025-10-01", 200)
    assert url.startswith(
        "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates?"
    )
    assert "page%5Bsize%5D=200" in url and "[" not in url and "]" not in url
    assert "filter=record_date:gte:2025-10-01,security_type_desc:eq:Marketable" in url
    for bad in (("10/01/2025", 10), ("2025-10-01", 0), ("2025-10-01", 5000)):
        with pytest.raises(ValueError):
            treasury.request_path(*bad)


def test_no_floats_in_json_ints_ok():
    rows = [treasury_row("2026-08-31", "Treasury Bills", 4)]
    assert (
        treasury.parse_response(json.dumps(json.loads(treasury_message(rows)))).series["Treasury Bills"][0].value == "4"
    )
