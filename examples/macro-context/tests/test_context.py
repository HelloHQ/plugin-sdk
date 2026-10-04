import json

import pytest

from conftest import FakeClock, FakeHost, ok
from fixtures import (
    WB_ERROR,
    ecb_message,
    sgfx_message,
    sgfx_row,
    treasury_message,
    treasury_row,
    wb_message,
    wb_row,
)
from macro_context import ecb, sgfx, worldbank
from macro_context.context import ContextRequest, fetch_context, parse_request
from macro_context.errors import FetchError, ValidationError
from macro_context.host import HttpResponse
from macro_context.money import assert_no_floats
from macro_context.sdk_host import SdkHost


def router(overrides=None):
    overrides = overrides or {}

    def handler(method, url, body):
        for needle, reply in overrides.items():
            if needle in url:
                return reply
        if "data-api.ecb.europa.eu" in url:
            key = url.split("/data/")[1].split("?")[0]
            if key.startswith("EXR/"):
                return ok(
                    ecb_message(["2026-10-01", "2026-10-02"], [1.1225, 1.1298], unit_id="USD", unit_name="US dollar")
                )
            if key.startswith("ICP/"):
                return ok(
                    ecb_message(
                        ["2025-11", "2025-12"],
                        [2.1, 1.9],
                        unit_id="PCCH",
                        unit_name="Percentage change",
                        freq_name="Monthly",
                    )
                )
            return ok(ecb_message(["2026-10-04"], [2.65]))
        if "api.worldbank.org" in url:
            code = url.split("/indicator/")[1].split("?")[0]
            iso = url.split("/country/")[1].split("/")[0]
            return ok(
                wb_message([wb_row("2025", 2.5, indicator=code, iso3=iso), wb_row("2024", 3, indicator=code, iso3=iso)])
            )
        if "fiscaldata.treasury.gov" in url:
            return ok(
                treasury_message(
                    [
                        treasury_row("2026-08-31", "Treasury Bills", "3.788"),
                        treasury_row("2026-08-31", "Treasury Notes", "3.345"),
                    ]
                )
            )
        if "data.gov.sg" in url:
            return ok(
                sgfx_message([sgfx_row(n, **{"2026Jul": "1.5", "2026Jun": "1.4"}) for n in sgfx.DEFAULT_CURRENCIES])
            )
        return None

    return handler


def run(handler=None, request=None, clock=None):
    clock = clock or FakeClock()
    host = FakeHost(handler or router())
    return fetch_context(host, request or ContextRequest(), clock, rand=lambda: 1.0), host, clock


def by_id(report):
    return {s["id"]: s for s in report["series"]}


def test_full_report_shape_asof_units_provenance():
    report, host, clock = run()
    assert report["issues"] == []
    series = by_id(report)
    assert len(series) == len(ecb.CATALOG) + 4 * 2 + 2 + len(sgfx.DEFAULT_CURRENCIES)
    mro = series["ecb.policy.mro"]
    assert (mro["as_of"], mro["latest"], mro["unit"], mro["unit_basis"]) == (
        "2026-10-04",
        "2.65",
        "Percent per annum",
        "source",
    )
    fx = series["ecb.fx.usd"]
    assert fx["unit_note"] == "units of USD per 1 EUR" and fx["as_of"] == "2026-10-02" and fx["latest"] == "1.1298"
    wb = series["wb.FP.CPI.TOTL.ZG.SGP"]
    assert (
        wb["country"] == "SGP" and wb["unit"] == "percent" and wb["unit_basis"] == "catalog" and wb["as_of"] == "2025"
    )
    ust = series["ust.avg_rate.treasury_bills"]
    assert ust["unit"] == "percent" and "not a market yield" in ust["title"] and ust["frequency"] == "monthly"
    mas = series["mas.fx.us_dollar"]
    assert mas["unit_multiplier"] is None and "no conversion" in mas["unit_note"] and mas["as_of"] == "2026-07"
    assert report["disclaimer"].endswith("not a forecast.")


@pytest.mark.parametrize(
    "sid", ["ecb.policy.mro", "wb.NY.GDP.MKTP.KD.ZG.USA", "ust.avg_rate.treasury_notes", "mas.fx.euro"]
)
def test_every_series_has_complete_provenance(sid):
    report, *_ = run()
    prov = by_id(report)[sid]["provenance"]
    assert set(prov) == {"source", "identifier", "reference", "fetched_at"}
    assert all(prov.values()) and prov["fetched_at"].endswith("Z")
    assert prov["reference"].startswith("/")


def test_categories_are_from_the_known_set_and_yield_is_present():
    from macro_context.series import CATEGORIES

    report, *_ = run()
    cats = {s["category"] for s in report["series"]}
    assert (
        cats <= set(CATEGORIES)
        and {"yield", "policy_rate", "fx", "inflation", "growth", "average_interest_rate"} == cats
    )


def test_report_is_json_serialisable_and_float_free():
    report, *_ = run()
    json.dumps(report)
    assert_no_floats(report)
    for s in report["series"]:
        assert isinstance(s["latest"], str) and all(isinstance(o["value"], str) for o in s["observations"])


def test_no_forecast_or_advice_fields_exist():
    report, *_ = run()
    text = json.dumps(report).lower()
    for word in ("forecast_value", "recommend", "buy", "sell", "projection"):
        assert word not in text.replace("not a forecast", "")


def test_one_failing_series_does_not_abort_the_others():
    handler = router({"D.USD.EUR": HttpResponse(500, ""), "FP.CPI.TOTL.ZG/": None})
    report, *_ = run(handler)
    codes = {i["subject"]: i["code"] for i in report["issues"]}
    assert codes["ecb.fx.usd"] == "rate_limited"  # 500 retried then gave up
    assert "ecb.fx.jpy" in by_id(report)


def test_worldbank_error_body_and_ecb_empty_are_reported():
    handler = router(
        {"/country/CHN/": ok(WB_ERROR), "EXR/D.GBP": ok(json.dumps({"dataSets": [{"series": {}}], "structure": {}}))}
    )
    report, *_ = run(handler)
    issues = {i["subject"]: i["code"] for i in report["issues"]}
    assert issues["FP.CPI.TOTL.ZG:CHN"] == "worldbank_error"
    assert issues["ecb.fx.gbp"] == "no_data"
    assert "wb.FP.CPI.TOTL.ZG.USA" in by_id(report)


def test_origin_denied_by_host_is_reported_not_raised():
    handler = router({"fiscaldata": FetchError("denied", code="permission_denied")})
    report, *_ = run(handler, ContextRequest(sections=("treasury",)))
    assert [i["code"] for i in report["issues"]] == ["permission_denied"] and report["series"] == []


def test_sections_limit_which_origins_are_contacted():
    _, host, _ = run(request=ContextRequest(sections=("sgfx",)))
    assert [c[1].split("/")[2] for c in host.calls] == ["data.gov.sg"]


def test_requests_target_only_the_four_declared_origins_over_https_get():
    _, host, _ = run()
    origins = {c[1].split("/")[2] for c in host.calls}
    assert origins == {"data-api.ecb.europa.eu", "api.worldbank.org", "api.fiscaldata.treasury.gov", "data.gov.sg"}
    assert all(c[0] == "GET" and c[1].startswith("https://") and c[3] == "" for c in host.calls)


def test_no_secrets_headers_or_key_params_anywhere():
    _, host, _ = run()
    for _m, url, headers, _b in host.calls:
        low = url.lower()
        assert "apikey" not in low and "api_key" not in low and "token" not in low and "key=" not in low
        assert not {h.lower() for h in headers} & {"authorization", "cookie", "x-api-key"}


def test_pacing_respects_documented_data_gov_sg_limit_and_cache():
    clock = FakeClock()
    stamps = []

    def stamping(m, u, b):
        stamps.append(clock.t)
        return router()(m, u, b)

    host2 = FakeHost(stamping)
    from macro_context.context import DEFAULT_POLICIES
    from macro_context.polite import PoliteClient

    client = PoliteClient(host2, clock, DEFAULT_POLICIES, cache_ttl_s=0)
    for _ in range(8):
        client.get(sgfx.url_for(100))
    for i, s in enumerate(stamps):
        assert len([x for x in stamps[i:] if x < s + 10]) <= 3  # documented 4 / 10 s, we use 3


def test_ecb_requests_are_sequential_and_paced():
    _, host, clock = run(request=ContextRequest(sections=("ecb",)))
    assert len(host.calls) == len(ecb.CATALOG)
    assert clock.t - 1000.0 >= len(ecb.CATALOG) - 1  # >= 1 s apart


def test_default_countries_and_indicators_each_get_a_request():
    _, host, _ = run(request=ContextRequest(sections=("worldbank",)))
    assert len(host.calls) == len(worldbank.DEFAULT_COUNTRIES) * len(worldbank.CATALOG)


def test_treasury_since_date_is_derived_from_the_clock():
    _, host, _ = run(request=ContextRequest(sections=("treasury",), treasury_months=2))
    assert "record_date:gte:2026-08-03" in host.calls[0][1]  # 2026-10-04 minus 62 days


def test_parse_request_validation():
    assert parse_request(None) == ContextRequest()
    ok_req = parse_request({"sections": ["ecb"], "worldbank_countries": ["sgp"], "fx_months": 3, "treasury_months": 1})
    assert ok_req.worldbank_countries == ("SGP",) and ok_req.fx_months == 3
    for bad in (
        {"nope": 1},
        {"sections": ["nasdaq"]},
        {"sections": []},
        {"sections": "ecb"},
        {"ecb_series": ["ecb.nope"]},
        {"worldbank_countries": ["../x"]},
        {"worldbank_countries": ["USA"] * 7},
        {"worldbank_indicators": ["X.Y"]},
        {"treasury_months": 0},
        {"treasury_months": True},
        {"treasury_months": 61},
        {"fx_months": 25},
        {"fx_currencies": [1]},
    ):
        with pytest.raises(ValidationError):
            parse_request(bad)


def test_macro_plugin_is_read_only():
    assert not hasattr(SdkHost, "propose")
    manifest = json.load(open(__import__("pathlib").Path(__file__).resolve().parent.parent / "manifest.json"))
    assert [p["id"] for p in manifest["permissions"]] == ["network:fetch"]
