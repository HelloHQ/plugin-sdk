import pytest

from fixtures import ECB_EMPTY, ecb_message
from macro_context import ecb
from macro_context.errors import ParseError


def test_valid_series_with_units_and_decimal_exactness():
    parsed = ecb.parse_response(ecb_message(["2026-10-01", "2026-10-02"], [2.65, 3.5921733451]))
    assert [(o.period, o.value) for o in parsed.observations] == [
        ("2026-10-01", "2.65"),
        ("2026-10-02", "3.5921733451"),
    ]
    assert (parsed.unit_code, parsed.unit_name, parsed.unit_multiplier) == ("PCPA", "Percent per annum", 1)
    assert parsed.frequency == "Daily" and parsed.source_title == "Test series"


def test_values_never_become_binary_floats():
    parsed = ecb.parse_response(ecb_message(["2026-10-01"], [0.1]))
    assert parsed.observations[0].value == "0.1"  # float(0.1) would not survive an exact round trip elsewhere


def test_periods_sorted_ascending_regardless_of_input_order():
    parsed = ecb.parse_response(ecb_message(["2026-10-02", "2026-10-01"], [2.0, 1.0]))
    assert [o.period for o in parsed.observations] == ["2026-10-01", "2026-10-02"]


def test_null_observations_skipped_and_all_null_is_no_data():
    parsed = ecb.parse_response(ecb_message(["2026-10-01", "2026-10-02"], [None, 1.5]))
    assert [o.period for o in parsed.observations] == ["2026-10-02"]
    with pytest.raises(ParseError) as err:
        ecb.parse_response(ecb_message(["2026-10-01"], [None]))
    assert err.value.code == "no_data"


def test_empty_result_is_no_data():
    with pytest.raises(ParseError) as err:
        ecb.parse_response(ECB_EMPTY)
    assert err.value.code == "no_data"


def test_unit_multiplier_exponent():
    parsed = ecb.parse_response(ecb_message(["2026-01"], [5.0], mult_id="6"))
    assert parsed.unit_multiplier == 1_000_000


def test_unit_name_optional_but_unit_required():
    assert ecb.parse_response(ecb_message(["x"], [1.0], unit_name=None)).unit_name is None
    with pytest.raises(ParseError):
        ecb.parse_response(ecb_message(["x"], [1.0], drop_unit=True))


def test_missing_multiplier_defaults_to_units():
    assert ecb.parse_response(ecb_message(["x"], [1.0], mult_id=None)).unit_multiplier == 1


@pytest.mark.parametrize(
    "text", ["", "null", "[]", "{", '{"dataSets": []}', '{"dataSets": [{}]}', '{"dataSets":[{"series":[]}]}']
)
def test_garbage_raises(text):
    with pytest.raises(ParseError):
        ecb.parse_response(text)


def test_two_series_is_an_error():
    with pytest.raises(ParseError):
        ecb.parse_response(ecb_message(["x"], [1.0], extra_series=True))


def test_structural_mutations_raise():
    import json

    base = json.loads(ecb_message(["2026-10-01"], [1.0]))
    mutations = [
        lambda d: d["structure"]["dimensions"]["observation"][0].update(id="REF_AREA"),
        lambda d: d["structure"]["dimensions"].pop("observation"),
        lambda d: d["structure"].pop("dimensions"),
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"]["observations"].update({"7": [1.0]}),  # no such period
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"]["observations"].update({"x": [1.0]}),
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"]["observations"].update({"0": []}),
        lambda d: (
            d["dataSets"][0]["series"]["0:0:0:0:0"]["observations"].update({"0": ["1.0"]})
            or d["dataSets"][0]["series"]["0:0:0:0:0"]["observations"].update({"0": [True]})
        ),
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"]["attributes"].__setitem__(0, 5),  # index out of range
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"].pop("attributes"),
        lambda d: d["dataSets"][0]["series"]["0:0:0:0:0"].pop("observations"),
        lambda d: d["dataSets"].append({}),
    ]
    for mutate in mutations:
        doc = json.loads(json.dumps(base))
        mutate(doc)
        with pytest.raises(ParseError):
            ecb.parse_response(json.dumps(doc))


def test_unknown_fields_ignored():
    import json

    doc = json.loads(ecb_message(["2026-10-01"], [1.5]))
    doc["header"]["new"] = {"x": 1}
    doc["structure"]["dimensions"]["series"][0]["extra"] = True
    assert ecb.parse_response(json.dumps(doc)).observations[0].value == "1.5"


def test_huge_input():
    periods = [f"p{i:05d}" for i in range(5000)]
    parsed = ecb.parse_response(ecb_message(periods, [float(i) for i in range(5000)]))
    assert len(parsed.observations) == 5000 and parsed.observations[-1].value == "4999.0"


def test_request_builders_and_catalog():
    spec = ecb.CATALOG_BY_ID["ecb.fx.usd"]
    assert (
        ecb.url_for(spec)
        == "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?lastNObservations=5&format=jsondata"
    )
    assert len(ecb.CATALOG_BY_ID) == len(ecb.CATALOG)
    for s in ecb.CATALOG:
        assert ecb.request_path(s)  # all keys pass the charset check
    bad = ecb.EcbSeriesSpec("x", "EXR", "D.USD/../x", "t", "fx", 1)
    with pytest.raises(ValueError):
        ecb.request_path(bad)
