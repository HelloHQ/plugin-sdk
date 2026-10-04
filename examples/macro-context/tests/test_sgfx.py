import pytest

from fixtures import sgfx_message, sgfx_row
from macro_context import sgfx
from macro_context.errors import ParseError


def rows():
    return [
        sgfx_row("US Dollar", **{"2026Jul": "1.2838", "2026Jun": "1.2943", "2026May": "1.30", "2005Dec": "n.a."}),
        sgfx_row("Euro", **{"2026Jul": "1.4781", "2026Jun": ""}),
    ]


def test_valid_latest_months_sorted_and_exact():
    parsed = sgfx.parse_response(sgfx_message(rows()), ("US Dollar", "Euro"), months=2)
    assert [(o.period, o.value) for o in parsed.series["US Dollar"]] == [("2026-06", "1.2943"), ("2026-07", "1.2838")]
    assert [(o.period, o.value) for o in parsed.series["Euro"]] == [("2026-07", "1.4781")]  # empty cell skipped


def test_old_text_history_is_ignored_unless_selected():
    parsed = sgfx.parse_response(sgfx_message(rows()), ("US Dollar",), months=3)
    assert [o.period for o in parsed.series["US Dollar"]] == ["2026-05", "2026-06", "2026-07"]


def test_non_numeric_value_in_selected_window_is_explicit_error():
    with pytest.raises(ParseError):
        sgfx.parse_response(sgfx_message(rows()), ("US Dollar",), months=4)  # reaches "n.a."


def test_numbers_as_json_numbers_also_accepted():
    msg = sgfx_message([sgfx_row("US Dollar", **{"2026Jul": 1.5})])
    assert sgfx.parse_response(msg, ("US Dollar",)).series["US Dollar"][0].value == "1.5"


def test_missing_currency_is_explicit():
    with pytest.raises(ParseError) as err:
        sgfx.parse_response(sgfx_message(rows()), ("Swiss Franc",))
    assert err.value.code == "series_not_found"


def test_row_without_observations_is_no_data():
    with pytest.raises(ParseError) as err:
        sgfx.parse_response(sgfx_message([sgfx_row("US Dollar", **{"2026Jul": ""})]), ("US Dollar",))
    assert err.value.code == "no_data"


def test_truncated_flag_when_total_exceeds_limit():
    assert sgfx.parse_response(sgfx_message(rows(), total=500), ("Euro",), limit=100).truncated is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "{}",
        '{"success": false}',
        '{"success": true}',
        '{"success":true,"result":{}}',
        '{"success":true,"result":{"records":[{"x":1}]}}',
    ],
)
def test_garbage_raises(text):
    with pytest.raises(ParseError):
        sgfx.parse_response(text, ("US Dollar",))


def test_period_parsing():
    assert sgfx._period("2026Jul") == "2026-07"
    assert sgfx._period("2026Xyz") is None and sgfx._period("DataSeries") is None and sgfx._period("_id") is None


def test_request_url_and_constants():
    assert (
        sgfx.url_for(100)
        == "https://data.gov.sg/api/action/datastore_search?resource_id=d_cdd73fd4341b345fa4307e44d6f82175&limit=100"
    )
    with pytest.raises(ValueError):
        sgfx.request_path(0)
    assert "no conversion" in sgfx.UNIT_NOTE


def test_huge_input():
    many = [sgfx_row(f"Cur {i}", **{"2026Jul": "1"}) for i in range(5000)]
    parsed = sgfx.parse_response(sgfx_message(many), ("Cur 4999",))
    assert parsed.series["Cur 4999"][0].value == "1"
