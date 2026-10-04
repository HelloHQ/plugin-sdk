"""Unit tests for the report model, both languages, and the three renderers."""

from __future__ import annotations

import json
import os
import re
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

import fixtures as fx
import pytest
from fixtures import CNY, EUR, GBP, IDR, JPY, KWD, SGD, USD
from hellohq_plugin_sdk import PluginError, UnsupportedFunction

import plugin

GOLDEN_DIR = Path(__file__).parent / "golden"
LANGS = ("en", "zh-Hans")


def check_golden(name: str, actual: str) -> None:
    """Compare against ``tests/golden/<name>``; ``UPDATE_GOLDEN=1`` rewrites."""
    path = GOLDEN_DIR / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing golden {name}; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8")


def generate(scenario: str, lang: str = "en"):
    return plugin.generate(fx.SCENARIOS[scenario], lang, now=fx.NOW)


def doc(scenario: str, lang: str, fmt: str) -> str:
    return generate(scenario, lang)["documents"][fmt]["content"]


def doc_from(context, lang: str = "en", fmt: str = "markdown") -> str:
    return plugin.generate(context, lang, now=fx.NOW)["documents"][fmt]["content"]


def model(context) -> dict:
    return plugin.build_report(context, fx.NOW)


def shown(context) -> dict[str, list[tuple[str, str]]]:
    """Portfolio id -> [(currency, exact amount)] for portfolios with amounts."""
    return {
        p["id"]: [(t["currency"], t["amount"]) for t in p["totals"]]
        for p in model(context)["portfolios"]
        if p["totals_status"] == "shown"
    }


def statuses(context) -> list[str]:
    return [p["totals_status"] for p in model(context)["portfolios"]]


# ── Golden outputs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scenario", sorted(fx.SCENARIOS))
def test_markdown_golden(scenario: str, lang: str) -> None:
    check_golden(f"{scenario}.{lang}.md", doc(scenario, lang, "markdown"))


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("fmt,ext", [("text", "txt"), ("html", "html")])
def test_text_and_html_golden(fmt: str, ext: str, lang: str) -> None:
    check_golden(f"family.{lang}.{ext}", doc("family", lang, fmt))


def test_model_golden() -> None:
    check_golden(
        "family.model.json",
        json.dumps(generate("family")["model"], indent=2, ensure_ascii=False) + "\n",
    )


# ── String table ────────────────────────────────────────────────────────────


def _placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_string_tables_have_identical_keys_and_placeholders() -> None:
    en, zh = plugin.STRINGS["en"], plugin.STRINGS["zh-Hans"]
    assert set(en) == set(zh)
    for key in en:
        assert _placeholders(en[key]) == _placeholders(zh[key]), key


def test_every_note_code_has_copy_in_both_languages() -> None:
    codes = {
        "basis", "app_differs", "asof", "currency", "rounding",
        "totals_not_provided", "negative", "precision", "names_unavailable",
        "truncated",
    }  # fmt: skip
    counted = {"missing_totals", "liabilities", "unclassified", "skipped"}
    counted |= {"unknown_currency"}
    for lang in LANGS:
        table = plugin.STRINGS[lang]
        for code in codes:
            assert f"note_{code}" in table, (lang, code)
        for code in counted:
            assert {f"note_{code}_one", f"note_{code}_other"} <= set(table), code


def test_zh_uses_consistent_terminology() -> None:
    text = "".join(doc(s, "zh-Hans", "markdown") for s in fx.SCENARIOS)
    for term in ("总资产", "负债", "净资产", "币种", "读取时间", "投资组合"):
        assert term in text, term
    # Variants this pack deliberately does not use.
    for variant in ("货币", "币别", "资产净值", "总负债", "债务", "截至"):
        assert variant not in text, variant
    # 净资产 appears only to say that net worth is not shown.
    assert text.count("净资产") == text.count("本报告不显示净资产")


def test_net_worth_is_never_claimed() -> None:
    for scenario in fx.SCENARIOS:
        en = doc(scenario, "en", "markdown")
        assert en.count("net worth") == en.count("It does not show net worth")


# ── Language selection ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag,expected",
    [
        (None, "en"),
        ("", "en"),
        ("en", "en"),
        ("EN-gb", "en"),
        ("en_US", "en"),
        (" en ", "en"),
        ("zh", "zh-Hans"),
        ("zh-Hans", "zh-Hans"),
        ("zh_CN", "zh-Hans"),
        ("ZH-SG", "zh-Hans"),
        ("zh-Hans-CN", "zh-Hans"),
    ],
)
def test_normalize_lang(tag, expected) -> None:
    assert plugin.normalize_lang(tag) == expected


@pytest.mark.parametrize(
    "tag", ["zh-Hant", "zh-TW", "zh-HK", "zh-Hant-TW", "fr", "xx", 5, ["en"], True]
)
def test_unsupported_lang_is_rejected_not_substituted(tag) -> None:
    with pytest.raises(PluginError) as err:
        plugin.normalize_lang(tag)
    assert err.value.code == "invalid_input"


@pytest.mark.parametrize("tag,lang", [("en-SG", "en"), ("zh-SG", "zh-Hans")])
def test_language_choice_reaches_every_output(tag: str, lang: str) -> None:
    out = plugin.generate(fx.FAMILY, tag, now=fx.NOW)
    assert out["lang"] == lang
    title = plugin.STRINGS[lang]["doc_title"]
    for d in out["documents"].values():
        assert title in d["content"]
        assert d["filename"].endswith(f"-{lang}" + Path(d["filename"]).suffix)
    other = "zh-Hans" if lang == "en" else "en"
    assert plugin.STRINGS[other]["doc_title"] not in out["documents"]["text"]["content"]
    assert f'<html lang="{lang}">' in out["documents"]["html"]["content"]


def test_model_does_not_depend_on_language() -> None:
    en = plugin.generate(fx.FAMILY, "en", now=fx.NOW)["model"]
    zh = plugin.generate(fx.FAMILY, "zh-Hans", now=fx.NOW)["model"]
    assert en == zh


# ── Currency identification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "currency_id,expected",
    [
        (USD, "USD"),
        (USD.upper(), "USD"),
        (f" {JPY} ", "JPY"),
        (IDR, "IDR"),  # built-in id, even when the currency list is absent
        (EUR, "EUR"),  # user row: name "Euro zone" is not code-shaped, the symbol is
        (KWD, "KWD"),  # import row, in the list
        ("document-import-currency-CHF", "CHF"),  # import row, not in the list
        ("document-import-currency-not a code", None),
        ("usd", "USD"),  # bare ISO-shaped id (mock-host)
        ("0b9a1c2d-0000-4000-8000-000000000000", None),  # unknown row id
        ("", None),
        (None, None),
        (5, None),
    ],
)
def test_resolve_currency(currency_id, expected) -> None:
    table = plugin._currency_table(fx.CURRENCY_LIST)
    assert plugin.resolve_currency(currency_id, table) == expected


def test_currency_table_reads_codes_only_never_rates() -> None:
    raw = [
        {"id": "a", "name": "usd", "symbol": "$", "rate": 1},
        {"id": "b", "name": "Pound sterling", "symbol": "£"},
        {"id": "d", "name": "Pound", "symbol": "£"},  # code-shaped, as in the app
        {"id": "c", "name": "Thai baht", "symbol": "THB"},
        {"id": "a", "name": "EUR"},  # duplicate id: first wins
        {"name": "NOID"},
        {"id": "e", "name": 5, "symbol": None},
        "junk",
    ]
    assert plugin._currency_table(raw) == {"a": "USD", "c": "THB", "d": "POUND"}
    assert plugin._currency_table(None) == {} and plugin._currency_table({}) == {}
    source = (Path(plugin.__file__)).read_text(encoding="utf-8")
    for access in ('.get("rate")', '["rate"]', ".get('rate')", "['rate']"):
        assert access not in source


def test_uuid_currency_ids_are_shown_as_codes_never_as_ids() -> None:
    for scenario in fx.SCENARIOS:
        for fmt in ("markdown", "text", "html"):
            out = doc(scenario, "en", fmt)
            for cid in (USD, CNY, SGD, JPY, EUR, GBP):
                assert cid not in out and cid[:8].upper() not in out


def test_unidentified_currency_is_not_shown_and_is_counted() -> None:
    unknown = "0b9a1c2d-0000-4000-8000-000000000000"
    context = fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: 5, unknown: 7}))
    m = model(context)
    assert shown(context) == {"a": [("USD", "5")]}
    assert {"code": "unknown_currency", "n": 1} in m["notes"]
    out = doc_from(context)
    assert "7.00" not in out
    assert "1 total is in a currency this plugin could not identify" in out


def test_portfolio_whose_currencies_are_all_unknown_is_unreadable() -> None:
    context = fx.ctx(fx.names(("a", "A")), fx.totals(a={"not-a-currency-id": 5}))
    assert statuses(context) == ["unreadable"]
    assert model(context)["data_status"] == "no_totals"
    assert "| A | — | Could not be read |" in doc_from(context)


def test_without_the_currency_list_built_in_ids_still_resolve() -> None:
    context = fx.ctx(
        fx.names(("a", "A")), fx.totals(a={USD: 1, EUR: 2}), currencies_read=None
    )
    assert shown(context) == {"a": [("USD", "1")]}
    assert {"code": "unknown_currency", "n": 1} in model(context)["notes"]


# ── Assets vs liabilities (the host total mixes them) ──────────────────────


def test_portfolio_with_liabilities_shows_no_amount() -> None:
    context = fx.ctx(
        fx.names(("home", "Home"), ("cash", "Cash")),
        fx.totals(home={SGD: 1_800_000.0}, cash={SGD: 50_000.0}),
        fx.counts(home=(1, 1), cash=(2, 0)),
    )
    m = model(context)
    assert statuses(context) == ["liabilities", "shown"]
    home = m["portfolios"][0]
    assert home["totals"] == [] and home["currencies"] == ["SGD"]
    assert home["liability_items"] == 1
    # The withheld amount is nowhere: not in the model, not in any document.
    assert "1800000" not in json.dumps(m)
    for lang in LANGS:
        for fmt in ("markdown", "text", "html"):
            out = doc_from(context, lang, fmt)
            assert "1,800,000" not in out and "1,850,000" not in out
    # The combined figure covers only the debt-free portfolio.
    assert m["combined_totals"] == [
        {
            "currency": "SGD",
            "total_assets": "50000.0",
            "portfolio_count": 1,
            "portfolios": [{"id": "cash", "name": "Cash"}],
        }
    ]
    assert {"code": "liabilities", "n": 1} in m["notes"]
    en = doc_from(context)
    assert "| Home | SGD | Not shown (includes liabilities) |" in en
    assert "- SGD 50,000.00: 1 portfolio (Cash)." in en
    zh = doc_from(context, "zh-Hans")
    assert "| Home | SGD | 未显示（含负债） |" in zh


def test_without_item_counts_no_amount_is_shown() -> None:
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.totals(a={USD: 10}, b={USD: 20}),
        counts_read=False,
    )
    m = model(context)
    assert statuses(context) == ["unclassified", "unclassified"]
    assert m["data_status"] == "withheld" and m["combined_totals"] == []
    assert {"code": "unclassified", "n": 2} in m["notes"]
    en = doc_from(context)
    assert plugin.STRINGS["en"]["sum_withheld"] in en
    assert not re.search(r"\d+\.\d\d", en.split("## Notes")[0])


def test_counts_for_some_portfolios_only() -> None:
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.totals(a={USD: 10}, b={USD: 20}),
        fx.counts(a=(1, 0)),
    )
    assert statuses(context) == ["shown", "unclassified"]
    assert model(context)["data_status"] == "partial"


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "a", "asset_items": 1},
        {"id": "a", "asset_items": 1, "debt_items": None},
        {"id": "a", "asset_items": 1, "debt_items": -1},
        {"id": "a", "asset_items": 1, "debt_items": True},
        {"id": "a", "asset_items": 1, "debt_items": 0.0},
        {"id": "a", "asset_items": "1", "debt_items": 0},
    ],
)
def test_malformed_counts_are_treated_as_unknown(entry) -> None:
    context = fx.ctx(
        fx.names(("a", "A")), fx.totals(a={USD: 1}), {"portfolios": [entry]}
    )
    assert statuses(context) == ["unclassified"]


def test_counts_read_may_be_a_bare_list() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(a={USD: 1}),
        [
            {"asset_items": 1, "debt_items": 0},
            "junk",
            {"id": "a", "asset_items": 1, "debt_items": 0},
        ],
    )
    assert statuses(context) == ["shown"]


def test_all_portfolios_with_liabilities() -> None:
    m = generate("all_liabilities")["model"]
    assert m["data_status"] == "withheld"
    assert m["summary"]["portfolios_with_totals"] == 0
    assert m["summary"]["portfolios_withheld"] == 2
    for lang in LANGS:
        out = doc("all_liabilities", lang, "markdown")
        assert plugin.STRINGS[lang]["sum_withheld"] in out
        assert "900,000" not in out and "30,000" not in out


# ── Report model ────────────────────────────────────────────────────────────


def test_model_is_language_neutral_and_json_clean() -> None:
    m = model(fx.FAMILY)
    assert json.loads(json.dumps(m)) == m
    assert m["schema"] == "hellohq.report-pack/2"
    assert m["generated_at"] == "2026-10-04T12:00:30Z"
    assert m["data_status"] == "partial"
    assert m["summary"] == {
        "portfolio_count": 5,
        "portfolios_with_totals": 2,
        "portfolios_withheld": 2,
        "currency_count": 4,
    }
    assert [e["currency"] for e in m["currency_exposure"]] == [
        "CNY",
        "JPY",
        "SGD",
        "USD",
    ]
    assert statuses(fx.FAMILY) == [
        "liabilities",
        "shown",
        "unclassified",
        "shown",
        "empty",
    ]


def test_amounts_are_exact_decimal_strings_never_summed_across_currencies() -> None:
    m = model(fx.ASSETS_ONLY)
    by_cur = {e["currency"]: e for e in m["combined_totals"]}
    assert by_cur["USD"]["total_assets"] == "3000.0"
    assert by_cur["USD"]["portfolio_count"] == 2
    assert by_cur["SGD"]["total_assets"] == "250.5"
    assert by_cur["EUR"]["total_assets"] == "99.99"
    assert len(by_cur) == 3  # one per currency, never a grand total
    # Floats do not leak binary noise into the model.
    one = model(fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: 0.1})))
    two = model(
        fx.ctx(fx.names(("a", "A"), ("b", "B")), fx.totals(a={USD: 0.1}, b={USD: 0.2}))
    )
    assert one["combined_totals"][0]["total_assets"] == "0.1"
    assert two["combined_totals"][0]["total_assets"] == "0.3"


def test_many_currencies_in_one_portfolio_stay_separate() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(a={USD: 1, CNY: 2, SGD: 3, GBP: 4, JPY: 5, EUR: 6, KWD: 7, IDR: 8}),
    )
    rows = shown(context)["a"]
    assert [c for c, _ in rows] == sorted(
        ["USD", "CNY", "SGD", "GBP", "JPY", "EUR", "KWD", "IDR"]
    )
    out = doc_from(context)
    assert "| A | KWD | 7.000 |" in out and "| A | JPY | 5 |" in out
    assert "| A | IDR | 8.00 |" in out


def test_zero_portfolios() -> None:
    m = model(fx.EMPTY)
    assert m["data_status"] == "no_portfolios"
    assert m["portfolios"] == [] and m["currency_exposure"] == []
    assert m["combined_totals"] == []
    for lang in LANGS:
        out = doc("empty", lang, "markdown")
        assert plugin.STRINGS[lang]["sum_no_portfolios"] in out
        assert plugin.STRINGS[lang]["portfolios_none"] in out
        assert plugin.STRINGS[lang]["exposure_none"] in out
        assert not re.search(
            r"\d+\.\d\d", out.split(plugin.STRINGS[lang]["h_summary"])[1]
        )


def test_portfolios_with_no_recorded_values() -> None:
    context = fx.ctx(fx.names(("a", "A"), ("b", "B")), fx.totals(a={}, b={}))
    m = model(context)
    assert statuses(context) == ["empty", "empty"]
    assert m["data_status"] == "no_totals"
    assert "| A | — | No recorded values |" in doc_from(context)


@pytest.mark.parametrize(
    "context", [None, {}, [], "x", 5, {"read:portfolio_names": "bad"}]
)
def test_garbage_context_degrades_to_no_portfolios(context) -> None:
    assert plugin.build_report(context, fx.NOW)["data_status"] == "no_portfolios"


def test_missing_totals_are_reported_missing_not_estimated() -> None:
    m = model(fx.NO_TOTALS)
    assert m["data_status"] == "no_totals"
    assert statuses(fx.NO_TOTALS) == ["missing", "missing"]
    assert {"code": "totals_not_provided"} in m["notes"]
    for lang in LANGS:
        out = doc("no_totals", lang, "markdown")
        s = plugin.STRINGS[lang]
        assert s["sum_no_totals"] in out
        assert s["not_available"] in out
        assert s["exposure_none"] in out
        # No amount-looking figure anywhere in the document.
        assert not re.search(r"\d{1,3}(,\d{3})*\.\d{2,3}", out), out


def test_partial_totals_distinguish_missing_from_empty() -> None:
    m = model(fx.PARTIAL)
    assert m["data_status"] == "partial"
    assert statuses(fx.PARTIAL) == ["shown", "missing", "empty"]
    assert {"code": "missing_totals", "n": 1} in m["notes"]
    en = doc("partial", "en", "markdown")
    assert "Not available" in en and "No recorded values" in en
    assert "Totals are not available" not in en  # singular form for exactly one
    assert "A total is not available for 1 portfolio" in en
    assert m["summary"]["portfolios_with_totals"] == 1


def test_complete_counts_empty_portfolios_as_complete() -> None:
    context = fx.ctx(fx.names(("a", "A"), ("b", "B")), fx.totals(a={USD: 1}, b={}))
    assert model(context)["data_status"] == "complete"


def test_negative_totals_are_shown_as_recorded() -> None:
    m = model(fx.NEGATIVE)
    assert {"code": "negative"} in m["notes"]
    en = doc("negative", "en", "markdown")
    assert "-350,000.75" in en
    assert "SGD -330,000.75" in en  # -350000.75 + 20000.0, same currency only
    assert "-0.00" not in en  # -0.004 USD rounds to zero, printed without a sign
    assert "| USD | 0.00 |" in en
    zh = doc("negative", "zh-Hans", "markdown")
    assert plugin.STRINGS["zh-Hans"]["note_negative"] in zh


def test_aggregated_only_portfolio_and_names_unavailable() -> None:
    context = fx.ctx(None, fx.totals(ptf_x={EUR: 10.0}))
    m = model(context)
    assert [p["id"] for p in m["portfolios"]] == ["ptf_x"]
    assert m["portfolios"][0]["name"] is None
    assert {"code": "names_unavailable"} in m["notes"]
    assert r"| ptf\_x | EUR | 10.00 |" in doc_from(context)


def test_unnamed_portfolio_with_known_names_read() -> None:
    context = fx.ctx(
        [
            {"id": "p1", "name": ""},
            {"id": "p1", "name": "dup"},
            {"name": "no id"},
            "junk",
        ],
        fx.totals(p1={USD: 1}),
    )
    m = model(context)
    assert len(m["portfolios"]) == 1  # duplicate id and malformed rows dropped
    assert m["portfolios"][0]["name"] is None
    assert "(unnamed portfolio)" not in doc_from(context)  # the id stands in
    assert "| p1 | USD | 1.00 |" in doc_from(context)


def test_bad_total_rows_are_skipped_and_counted() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {
                    "id": "a",
                    "totals": [
                        {"currency_id": USD, "total": 5},
                        {"currency_id": USD, "total": float("nan")},
                        {"currency_id": EUR, "total": float("inf")},
                        {"currency_id": GBP, "total": True},
                        {"currency_id": CNY, "total": None},
                        {"currency_id": JPY, "total": "1e999"},
                        {"currency_id": 5, "total": 1},
                        {"total": 1},
                        "junk",
                    ],
                },
                {"totals": []},
                "junk",
            ]
        },
        fx.counts(a=(1, 0)),
    )
    m = model(context)
    assert shown(context) == {"a": [("USD", "5")]}
    assert {"code": "skipped", "n": 10} in m["notes"]


def test_duplicate_aggregate_entries_keep_the_first() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {"id": "a", "totals": [{"currency_id": USD, "total": 1}]},
                {"id": "a", "totals": [{"currency_id": USD, "total": 99}]},
            ]
        },
        fx.counts(a=(1, 0)),
    )
    assert shown(context) == {"a": [("USD", "1")]}
    assert {"code": "skipped", "n": 1} in model(context)["notes"]


def test_rows_of_one_currency_under_two_ids_are_summed_exactly() -> None:
    # A built-in row and a bare ISO id both resolve to USD.
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {
                    "id": "a",
                    "totals": [
                        {"currency_id": USD, "total": 0.1},
                        {"currency_id": "usd", "total": 0.2},
                    ],
                }
            ]
        },
        fx.counts(a=(2, 0)),
    )
    assert shown(context) == {"a": [("USD", "0.3")]}


def test_aggregated_read_may_be_a_bare_list() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        [{"id": "a", "totals": [{"currency_id": USD, "total": 2}]}],
        fx.counts(a=(1, 0)),
    )
    assert model(context)["data_status"] == "complete"


def test_now_is_converted_to_utc() -> None:
    sgt = datetime(2026, 10, 4, 20, 5, 59, 999, tzinfo=timezone(timedelta(hours=8)))
    assert plugin.build_report(fx.EMPTY, sgt)["generated_at"] == "2026-10-04T12:05:59Z"


# ── Number formatting and rounding ──────────────────────────────────────────


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        ("1234567.891", "USD", "1,234,567.89"),
        ("-1234.5", "SGD", "-1,234.50"),
        ("0.125", "USD", "0.12"),  # half-even: 2 is even
        ("0.135", "USD", "0.14"),  # half-even: 4 is even
        ("0.145", "USD", "0.14"),
        ("-0.125", "USD", "-0.12"),
        ("0.1251", "USD", "0.13"),  # above half: always up
        ("-0.004", "USD", "0.00"),  # no negative zero
        ("0", "CNY", "0.00"),
        ("3500000", "JPY", "3,500,000"),
        ("2.5", "KRW", "2"),
        ("3.5", "JPY", "4"),
        ("1.2345", "KWD", "1.234"),
        ("1.2355", "KWD", "1.236"),
        ("1000000000000", "IDR", "1,000,000,000,000.00"),
        (
            "999999999999999999999999999999",
            "USD",
            "999,999,999,999,999,999,999,999,999,999.00",
        ),
        ("0.1", "XYZ", "0.10"),  # unknown code: two places
    ],
)
def test_format_amount(amount, currency, expected) -> None:
    assert plugin.format_amount(amount, currency) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (5, Decimal(5)),
        (-0.0, Decimal(0)),
        (1.5, Decimal("1.5")),
        (0.1, Decimal("0.1")),
        (215340.126, Decimal("215340.126")),
        ("12.50", Decimal("12.50")),
        (" 7 ", Decimal(7)),
        (10**30 - 1, Decimal(10**30 - 1)),
        (9.999e30, Decimal("9.999E+30")),
        (True, None),
        (None, None),
        ("abc", None),
        ("NaN", None),
        ("Infinity", None),
        (float("nan"), None),
        (float("-inf"), None),
        (10**31, None),
        (1e31, None),
        ([1], None),
    ],
)
def test_as_decimal(value, expected) -> None:
    assert plugin._as_decimal(value) == expected


def test_very_large_sums_are_exact() -> None:
    big = 10**29
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")), fx.totals(a={USD: big}, b={USD: 1})
    )
    m = model(context)
    assert m["combined_totals"][0]["total_assets"] == str(big + 1)
    assert "100,000,000,000,000,000,000,000,000,001.00" in doc_from(context)


def test_amounts_beyond_double_precision_are_flagged() -> None:
    # 2**53 minor units is where a JSON double stops holding every cent.
    limit_usd = Decimal(2**53) / 100
    below = fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: int(limit_usd) - 1}))
    above = fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: int(limit_usd) + 1}))
    assert {"code": "precision"} not in model(below)["notes"]
    assert {"code": "precision"} in model(above)["notes"]
    # JPY has no minor unit, so the same number is still exact.
    jpy = fx.ctx(fx.names(("a", "A")), fx.totals(a={JPY: int(limit_usd) + 1}))
    assert {"code": "precision"} not in model(jpy)["notes"]
    assert "last digits may be inexact" in doc_from(above)
    # Negative amounts count by magnitude.
    neg = fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: -(int(limit_usd) + 1)}))
    assert {"code": "precision"} in model(neg)["notes"]


def test_combined_total_is_rounded_once_from_the_exact_sum() -> None:
    # 0.005 + 0.005: each row rounds (half-even) to 0.00, the sum is 0.01.
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")), fx.totals(a={USD: 0.005}, b={USD: 0.005})
    )
    out = doc_from(context)
    assert "| A | USD | 0.00 |" in out and "| B | USD | 0.00 |" in out
    assert "- USD 0.01: sum of 2 portfolios (A, B)." in out
    assert plugin.STRINGS["en"]["note_rounding"] in out


# ── Provenance, currency, honesty ───────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGS)
def test_every_figure_carries_portfolio_currency_and_read_time(lang: str) -> None:
    md = doc("family", lang, "markdown")
    rows = [
        line
        for line in md.splitlines()
        if re.match(r"\| .* \| [A-Z]{3} \| [-\d,.]+ \|", line)
    ]
    assert len(rows) == 5  # Investments x3 + Cash Savings x2
    for row in rows:
        assert row.rstrip().endswith("| 2026-10-04 12:00 |")
    # The combined (summary) figures name the currency and the read time too.
    combined = [
        line for line in md.splitlines() if re.match(r"- [A-Z]{3} [-\d,.]+[:：]", line)
    ]
    assert len(combined) == 3
    for line in combined:
        assert "2026-10-04 12:00 UTC" in line


def test_summary_figures_list_their_source_portfolios() -> None:
    md = doc("family", "en", "markdown")
    assert (
        "- SGD 140,000.00: sum of 2 portfolios (Investments, Cash Savings). "
        "Read at 2026-10-04 12:00 UTC." in md
    )
    assert (
        "- JPY 3,500,000: 1 portfolio (Investments). Read at 2026-10-04 12:00 UTC."
        in md
    )
    # The mortgaged home's SGD and the unclassified business's USD are not in
    # any combined figure.
    assert "1,390,000" not in md and "264,840" not in md


def test_more_than_five_names_are_summarised() -> None:
    rows = [(f"p{i}", f"Portfolio {i}") for i in range(8)]
    context = fx.ctx(fx.names(*rows), fx.totals(**{pid: {USD: 1} for pid, _ in rows}))
    en = doc_from(context, "en")
    assert "Portfolio 4, and 3 more" in en
    zh = doc_from(context, "zh-Hans")
    assert "Portfolio 4、等另外 3 个" in zh


# The pack informs; it never recommends. Everything except the fixed
# disclaimer (which names advice only to disclaim it) must be free of
# recommendation language.
_BANNED = {
    "en": [
        "should",
        "recommend",
        "consider",
        "advise",
        "suggest",
        "buy",
        "sell",
        "rebalanc",
        "invest in",
        "you need to",
        "must",
        "opportunit",
    ],
    "zh-Hans": [
        "建议",
        "应该",
        "应当",
        "推荐",
        "考虑",
        "买入",
        "卖出",
        "再平衡",
        "务必",
        "需要您",
        "机会",
    ],
}


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scenario", sorted(fx.SCENARIOS))
def test_no_recommendation_language_outside_the_disclaimer(
    scenario: str, lang: str
) -> None:
    strings = plugin.STRINGS[lang]
    text = doc(scenario, lang, "text").replace(strings["disclaimer"], "")
    # Ignore user-supplied names: only fixed copy is under our control.
    for p in model(fx.SCENARIOS[scenario])["portfolios"]:
        text = text.replace(p["name"] or "", "")
    lowered = text.lower()
    for word in _BANNED[lang]:
        assert word not in lowered, (scenario, lang, word)


def test_no_string_in_the_table_recommends_anything() -> None:
    for lang in LANGS:
        for key, text in plugin.STRINGS[lang].items():
            if key == "disclaimer":
                continue
            for word in _BANNED[lang]:
                assert word not in text.lower(), (lang, key, word)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scenario", sorted(fx.SCENARIOS))
def test_disclaimer_is_always_present_and_last(scenario: str, lang: str) -> None:
    for fmt in ("markdown", "text", "html"):
        out = doc(scenario, lang, fmt)
        fragment = plugin.STRINGS[lang]["disclaimer"][:12]
        assert fragment in out
    md = doc(scenario, lang, "markdown").rstrip().splitlines()[-1]
    assert md.replace("\\", "") == plugin.STRINGS[lang]["disclaimer"]


def test_disclaimer_says_information_only() -> None:
    en = plugin.STRINGS["en"]["disclaimer"]
    assert (
        "for information only" in en
        and "not financial, investment, tax or legal advice" in en
    )
    zh = plugin.STRINGS["zh-Hans"]["disclaimer"]
    assert "仅供信息参考" in zh and "不构成财务、投资、税务或法律建议" in zh


# ── Hostile / awkward names ─────────────────────────────────────────────────


def _table_rows(md: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith("|")]


def _unescaped_pipes(line: str) -> int:
    return len(re.findall(r"(?<!\\)\|", line))


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scenario", ["special", "family"])
def test_markdown_escapes_names_and_keeps_tables_intact(
    scenario: str, lang: str
) -> None:
    md = doc(scenario, lang, "markdown")
    for table in re.split(r"\n\n", md):
        rows = _table_rows(table)
        if rows:
            counts = {_unescaped_pipes(r) for r in rows}
            assert len(counts) == 1, rows  # same column count on every row
    if scenario == "special":
        assert r"Savings \| 2026 \*draft\* \[x\](http\://evil) \<b\>bold\</b\>" in md
    assert not re.search(r"(?<!\\)<", md)  # no unescaped angle bracket survives


def test_control_and_bidi_characters_are_stripped() -> None:
    m = model(fx.SPECIAL)
    cjk = next(p for p in m["portfolios"] if p["id"] == "p3")
    assert cjk["name"] == "家庭基金 evil line2"
    for fmt in ("markdown", "text", "html"):
        out = doc("special", "en", fmt)
        assert not re.search("[\u202a-\u202e\u2066-\u2069\x00-\x08\x0b-\x1f]", out)


def test_very_long_names_are_shortened_in_documents_but_not_in_the_model() -> None:
    m = model(fx.SPECIAL)
    long_name = next(p for p in m["portfolios"] if p["id"] == "p2")["name"]
    assert long_name == "A" * 120
    assert {"code": "truncated"} in m["notes"]
    en = doc("special", "en", "markdown")
    assert "A" * 120 not in en
    assert "A" * 79 + "…" in en
    assert plugin.STRINGS["en"]["note_truncated"] in en


def test_name_truncation_is_by_character_not_byte() -> None:
    name = "家" * 120
    context = fx.ctx(fx.names(("a", name)), fx.totals(a={CNY: 1}))
    out = doc_from(context, "zh-Hans")
    assert "家" * 79 + "…" in out and "家" * 80 not in out


def test_html_is_escaped_self_contained_and_well_formed() -> None:
    html_doc = doc("special", "en", "html")
    assert "<script" not in html_doc.lower().replace("&lt;script", "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html_doc
    assert "http://evil" in html_doc  # shown as text inside the name...
    parser = _Collect()
    parser.feed(html_doc)
    parser.close()
    # ...but never as a link, image, script or other fetchable resource.
    assert parser.tags.isdisjoint(
        {"a", "img", "script", "iframe", "link", "object", "form"}
    )
    assert not any(a in parser.attrs for a in ("href", "src", "action"))
    assert parser.lang == "en"
    assert parser.csp and "default-src 'none'" in parser.csp
    assert parser.depth == 0  # balanced


class _Collect(HTMLParser):
    VOID: ClassVar[set[str]] = {"meta", "br", "hr", "img", "link", "input"}

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attrs: set[str] = set()
        self.lang = None
        self.csp = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs.update(k for k, _ in attrs)
        d = dict(attrs)
        if tag == "html":
            self.lang = d.get("lang")
        if tag == "meta" and d.get("http-equiv") == "Content-Security-Policy":
            self.csp = d.get("content")
        if tag not in self.VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        self.depth -= 1


def test_html_declares_the_document_language() -> None:
    assert '<html lang="en">' in doc("family", "en", "html")
    assert '<html lang="zh-Hans">' in doc("family", "zh-Hans", "html")


def test_text_underline_matches_wide_characters() -> None:
    zh = doc("family", "zh-Hans", "text").splitlines()
    assert zh[0] == "家庭会议报告" and zh[1] == "=" * 12


# ── generate() envelope ─────────────────────────────────────────────────────


def test_generate_envelope_and_filenames() -> None:
    out = plugin.generate(fx.FAMILY, "zh_CN", now=fx.NOW)
    assert out["lang"] == "zh-Hans" and out["data_status"] == "partial"
    docs = out["documents"]
    assert set(docs) == {"markdown", "text", "html"}
    assert docs["markdown"]["filename"] == "hellohq-family-report-2026-10-04-zh-Hans.md"
    assert docs["text"]["filename"].endswith(".txt")
    assert docs["html"]["filename"].endswith(".html")
    for d in docs.values():
        # Safe for the host's save bridge (no separators, no "..", <= 255).
        assert not set("/\\\x00") & set(d["filename"]) and ".." not in d["filename"]
        assert len(d["filename"]) <= 255 and d["mime"].endswith("charset=utf-8")


def test_generate_without_now_uses_the_current_time() -> None:
    out = plugin.generate(fx.EMPTY, "en")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out["generated_at"])


def test_control_characters_inside_names_are_dropped() -> None:
    context = fx.ctx(fx.names(("a", "Ca\x00sh\x07 \x1b[31mred")), fx.totals(a={USD: 1}))
    assert model(context)["portfolios"][0]["name"] == "Cash [31mred"


def test_dispatch_rejects_unknown_functions_and_tolerates_odd_arguments() -> None:
    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", {"context": {}, "input": {"function": "nope"}})
    for odd in (None, "x", {}, {"input": "x"}, {"input": {"args": "x"}}):
        assert plugin.dispatch("run", odd)["data_status"] == "no_portfolios"
