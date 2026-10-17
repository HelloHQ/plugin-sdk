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

import report_pack as plugin

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
    """Portfolio id -> [(currency, exact total assets)] for portfolios with amounts."""
    return {
        p["id"]: [(t["currency"], t["assets"]) for t in p["totals"]]
        for p in model(context)["portfolios"]
        if p["totals_status"] == "shown"
    }


def totals_of(context, pid: str) -> list[dict]:
    """The shown ``totals`` entries of one portfolio."""
    return next(p for p in model(context)["portfolios"] if p["id"] == pid)["totals"]


def figures(context, pid: str) -> dict[str, tuple[str, str, str]]:
    """Currency -> (assets, liabilities, net worth) as exact strings."""
    return {
        t["currency"]: (t["assets"], t["liabilities"], t["net_worth"])
        for t in totals_of(context, pid)
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
        "truncated", "networth",
    }  # fmt: skip
    counted = {"missing_totals", "liabilities", "unclassified", "skipped", "legacy"}
    counted |= {"unknown_currency"}
    for lang in LANGS:
        table = plugin.STRINGS[lang]
        for code in codes:
            assert f"note_{code}" in table, (lang, code)
        for code in counted:
            assert {f"note_{code}_one", f"note_{code}_other"} <= set(table), code


def test_zh_uses_consistent_terminology() -> None:
    text = "".join(doc(s, "zh-Hans", "markdown") for s in fx.SCENARIOS)
    for term in ("总资产", "负债", "净资产", "币种", "读取时间", "投资组合", "截至"):
        assert term in text, term
    # Variants this pack deliberately does not use.
    for variant in ("货币", "币别", "资产净值", "总负债", "债务", "截止"):
        assert variant not in text, variant
    # The three new labels are the column headers of the amounts table.
    zh = doc("family", "zh-Hans", "markdown")
    assert "| 投资组合 | 币种 | 总资产 | 截至 | 负债 | 截至 | 净资产 |" in zh


def test_english_labels_are_the_agreed_ones() -> None:
    en = doc("family", "en", "markdown")
    assert (
        "| Portfolio | Currency | Total assets | As of | Total liabilities | As of | "
        "Net worth |" in en
    )


@pytest.mark.parametrize("scenario", sorted(fx.SCENARIOS))
def test_net_worth_is_assets_minus_liabilities_within_one_currency(
    scenario: str,
) -> None:
    m = generate(scenario)["model"]
    for p in m["portfolios"]:
        for t in p["totals"]:
            net = Decimal(t["assets"]) - Decimal(t["liabilities"])
            assert Decimal(t["net_worth"]) == net, (p["id"], t)
    for e in m["combined_totals"]:
        net = Decimal(e["total_assets"]) - Decimal(e["total_liabilities"])
        assert Decimal(e["net_worth"]) == net, e
        # Only rows of this currency went into it.
        rows = [
            t
            for p in m["portfolios"]
            for t in p["totals"]
            if t["currency"] == e["currency"]
        ]
        assert sum(Decimal(t["assets"]) for t in rows) == Decimal(e["total_assets"])
        assert e["portfolio_count"] == len(rows)


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
    assert "| A | — | Could not be read | — | — | — | — |" in doc_from(context)


def test_without_the_currency_list_built_in_ids_still_resolve() -> None:
    context = fx.ctx(
        fx.names(("a", "A")), fx.totals(a={USD: 1, EUR: 2}), currencies_read=None
    )
    assert shown(context) == {"a": [("USD", "1")]}
    assert {"code": "unknown_currency", "n": 1} in model(context)["notes"]


# ── Assets, liabilities and net worth (the host's split) ───────────────────


def test_family_with_a_mortgage_shows_assets_liabilities_and_net_worth() -> None:
    context = fx.ctx(
        fx.names(("home", "Family Home"), ("cash", "Cash")),
        fx.totals(home={SGD: ("1800000", "1100000.25")}, cash={SGD: "50000"}),
    )
    m = model(context)
    assert statuses(context) == ["shown", "shown"]
    assert m["data_status"] == "complete"
    assert figures(context, "home") == {"SGD": ("1800000", "1100000.25", "699999.75")}
    assert m["combined_totals"] == [
        {
            "currency": "SGD",
            "total_assets": "1850000",
            "total_liabilities": "1100000.25",
            "net_worth": "749999.75",
            "portfolio_count": 2,
            "portfolios": [
                {"id": "home", "name": "Family Home"},
                {"id": "cash", "name": "Cash"},
            ],
        }
    ]
    en = doc_from(context)
    assert (
        "| Family Home | SGD | 1,800,000.00 | 2026-09-30 | 1,100,000.25 | "
        "2026-09-15 | 699,999.75 |" in en
    )
    assert "| Cash | SGD | 50,000.00 | 2026-09-30 | 0.00 | No items | 50,000.00 |" in en
    assert (
        "- SGD: total assets 1,850,000.00; total liabilities 1,100,000.25; "
        "net worth 749,999.75. Sum of 2 portfolios (Family Home, Cash). "
        "Read at 2026-10-04 12:00 UTC." in en
    )
    zh = doc_from(context, "zh-Hans")
    assert (
        "| Family Home | SGD | 1,800,000.00 | 2026-09-30 | 1,100,000.25 | "
        "2026-09-15 | 699,999.75 |" in zh
    )
    assert "| Cash | SGD | 50,000.00 | 2026-09-30 | 0.00 | 无项目 | 50,000.00 |" in zh
    assert (
        "- SGD：总资产 1,850,000.00；负债 1,100,000.25；净资产 749,999.75。"
        "2 个投资组合之和（Family Home、Cash）。读取时间 2026-10-04 12:00 UTC。" in zh
    )


def test_no_portfolio_is_withheld_merely_for_having_liabilities() -> None:
    m = generate("all_liabilities")["model"]
    assert m["data_status"] == "complete"
    assert m["summary"]["portfolios_with_totals"] == 2
    assert m["summary"]["portfolios_withheld"] == 0
    assert all(p["withheld"] is None for p in m["portfolios"])
    assert not {n["code"] for n in m["notes"]} & {"liabilities", "unclassified"}
    for lang in LANGS:
        out = doc("all_liabilities", lang, "markdown")
        assert plugin.STRINGS[lang]["sum_withheld"] not in out
        for label in ("withheld_liabilities", "withheld_unclassified"):
            assert plugin.STRINGS[lang][label] not in out
        assert "900,000.00" in out and "30,000.25" in out


def test_split_rows_ignore_the_item_counts() -> None:
    base = (fx.names(("a", "A")), fx.totals(a={USD: ("100", "40")}))
    for counts_read in (False, {}, fx.counts(a=(1, 5)), fx.counts(a=(0, 0)), "junk"):
        context = fx.ctx(*base, counts_read=counts_read)
        assert statuses(context) == ["shown"], counts_read
        assert figures(context, "a") == {"USD": ("100", "40", "60")}


def test_net_worth_is_never_added_across_currencies() -> None:
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.totals(
            a={USD: ("1000", "250"), SGD: ("500", "900")},
            b={USD: ("10", "0"), SGD: ("0", "100.5")},
        ),
    )
    m = model(context)
    by_cur = {e["currency"]: e for e in m["combined_totals"]}
    assert set(by_cur) == {"SGD", "USD"}  # one entry per currency, no grand total
    assert (
        by_cur["USD"]["total_assets"],
        by_cur["USD"]["total_liabilities"],
        by_cur["USD"]["net_worth"],
    ) == ("1010", "250", "760")
    assert (
        by_cur["SGD"]["total_assets"],
        by_cur["SGD"]["total_liabilities"],
        by_cur["SGD"]["net_worth"],
    ) == ("500", "1000.5", "-500.5")
    assert figures(context, "a") == {
        "SGD": ("500", "900", "-400"),
        "USD": ("1000", "250", "750"),
    }


def test_negative_net_worth_is_shown_as_recorded() -> None:
    context = fx.ctx(
        fx.names(("a", "Underwater")), fx.totals(a={USD: ("400000", "650000.5")})
    )
    assert figures(context, "a") == {"USD": ("400000", "650000.5", "-250000.5")}
    assert {"code": "negative"} in model(context)["notes"]
    en = doc_from(context)
    assert "| Underwater | USD | 400,000.00 | 2026-09-30 | 650,000.50 |" in en
    assert "| 2026-09-15 | -250,000.50 |" in en
    assert "net worth -250,000.50" in en


def test_debt_free_family_has_zero_liabilities_and_no_items_date() -> None:
    m = generate("assets_only")["model"]
    assert m["data_status"] == "complete"
    for p in m["portfolios"]:
        for t in p["totals"]:
            assert Decimal(t["liabilities"]) == 0 and t["liabilities_as_of"] is None
            assert t["net_worth"] == t["assets"]
    assert {"code": "negative"} not in m["notes"]
    en = doc("assets_only", "en", "markdown")
    assert "| Personal | SGD | 250.50 | 2026-09-30 | 0.00 | No items | 250.50 |" in en


def test_zero_liabilities_not_the_same_as_no_liability_items() -> None:
    # A liability item recorded at 0 has a date; no liability item has none.
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(
            a={
                USD: fx.split_row(
                    USD, "5", "0", debts_as_of="2026-08-01T00:00:00.000Z"
                ),
                SGD: fx.split_row(SGD, "5", "0"),
            }
        ),
    )
    en = doc_from(context)
    assert "| A | SGD | 5.00 | 2026-09-30 | 0.00 | No items | 5.00 |" in en
    assert "| A | USD | 5.00 | 2026-09-30 | 0.00 | 2026-08-01 | 5.00 |" in en


def test_a_portfolio_with_only_liabilities() -> None:
    context = fx.ctx(fx.names(("a", "Loan")), fx.totals(a={USD: ("0", "3000")}))
    assert figures(context, "a") == {"USD": ("0", "3000", "-3000")}
    en = doc_from(context)
    assert "| Loan | USD | 0.00 | No items | 3,000.00 | 2026-09-15 | -3,000.00 |" in en


def test_as_of_dates_come_from_the_host_per_side() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(
            a={
                USD: fx.split_row(
                    USD,
                    "10",
                    "4",
                    assets_as_of="2026-04-10T00:00:00.000Z",
                    debts_as_of="2025-12-31T23:30:00-05:00",  # 2026-01-01 in UTC
                ),
            }
        ),
    )
    (t,) = totals_of(context, "a")
    assert (t["assets_as_of"], t["liabilities_as_of"]) == ("2026-04-10", "2026-01-01")
    # The "read at" time is the read time, not any value's date.
    assert model(context)["generated_at"] == "2026-10-04T12:00:30Z"
    assert "| A | USD | 10.00 | 2026-04-10 | 4.00 | 2026-01-01 | 6.00 |" in doc_from(
        context
    )


def test_split_rows_of_one_currency_under_two_ids_sum_exactly_and_keep_the_latest_date() -> (
    None
):
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(
            a={
                USD: fx.split_row(
                    USD,
                    "0.1",
                    "0.4",
                    "2026-03-01T00:00:00.000Z",
                    "2026-01-01T00:00:00Z",
                ),
                "usd": fx.split_row(
                    "usd", "0.2", "0.5", "2026-02-01T00:00:00.000Z", "2026-06-01"
                ),
            }
        ),
    )
    (t,) = totals_of(context, "a")
    assert (t["assets"], t["liabilities"], t["net_worth"]) == ("0.3", "0.9", "-0.6")
    assert (t["assets_as_of"], t["liabilities_as_of"]) == ("2026-03-01", "2026-06-01")


def test_mixed_currencies_with_zero_and_three_decimals() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(
            a={
                JPY: ("3500000", "1250000"),
                KWD: ("12.3455", "2.0005"),
                USD: ("1.005", "0.004"),
            }
        ),
    )
    assert figures(context, "a") == {
        "JPY": ("3500000", "1250000", "2250000"),
        "KWD": ("12.3455", "2.0005", "10.3450"),  # exact; scale is kept
        "USD": ("1.005", "0.004", "1.001"),
    }
    en = doc_from(context)
    # Half to even at each currency's usual places; net is exact before rounding.
    assert (
        "| A | JPY | 3,500,000 | 2026-09-30 | 1,250,000 | 2026-09-15 | 2,250,000 |"
        in en
    )
    assert "| A | KWD | 12.346 | 2026-09-30 | 2.000 | 2026-09-15 | 10.345 |" in en
    assert "| A | USD | 1.00 | 2026-09-30 | 0.00 | 2026-09-15 | 1.00 |" in en


def test_sums_of_1e30_size_are_exact_decimal_not_float() -> None:
    big = "999999999999999999999999999999"  # 10**30 - 1
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.totals(
            a={USD: (big, "1")},
            b={USD: ("1.25", big)},
        ),
    )
    (e,) = model(context)["combined_totals"]
    assert e["total_assets"] == "1000000000000000000000000000000.25"
    assert e["total_liabilities"] == "1000000000000000000000000000000"
    assert e["net_worth"] == "0.25"
    assert figures(context, "a") == {
        "USD": (big, "1", "999999999999999999999999999998")
    }
    assert figures(context, "b") == {
        "USD": ("1.25", big, "-999999999999999999999999999997.75")
    }
    en = doc_from(context)
    assert "999,999,999,999,999,999,999,999,999,999.00" in en
    assert "-999,999,999,999,999,999,999,999,999,997.75" in en
    # Exact strings carry no double-precision caveat.
    assert {"code": "precision"} not in model(context)["notes"]
    # A double could not hold these (its nearest value is 10**30 off by 2**47).
    assert float(big) != int(big)


def test_exact_digits_survive_beyond_2_pow_53() -> None:
    context = fx.ctx(
        fx.names(("a", "A")), fx.totals(a={USD: ("90071992547409.93", "0.07")})
    )
    assert figures(context, "a") == {
        "USD": ("90071992547409.93", "0.07", "90071992547409.86")
    }
    assert {"code": "precision"} not in model(context)["notes"]


@pytest.mark.parametrize(
    "overrides,removed",
    [
        # Present but not exact decimal strings.
        ({"assets": 5, "debts": "0"}, ()),
        ({"assets": 5.5, "debts": "0"}, ()),
        ({"assets": "5", "debts": 0}, ()),
        ({"assets": "1e3", "debts": "0"}, ()),
        ({"assets": "NaN", "debts": "0"}, ()),
        ({"assets": "Infinity", "debts": "0"}, ()),
        ({"assets": "-Infinity", "debts": "0"}, ()),
        ({"assets": "0x10", "debts": "0"}, ()),
        ({"assets": "1_000", "debts": "0"}, ()),
        ({"assets": " 5", "debts": "0"}, ()),
        ({"assets": "5 ", "debts": "0"}, ()),
        ({"assets": "+5", "debts": "0"}, ()),
        ({"assets": "5.", "debts": "0"}, ()),
        ({"assets": ".5", "debts": "0"}, ()),
        ({"assets": "1,000", "debts": "0"}, ()),
        ({"assets": "", "debts": "0"}, ()),
        # Arabic-Indic digit three:
        ({"assets": "\u0663", "debts": "0"}, ()),
        ({"assets": True, "debts": "0"}, ()),
        ({"assets": None, "debts": "0"}, ()),
        ({"assets": ["5"], "debts": "0"}, ()),
        ({"assets": {"v": "5"}, "debts": "0"}, ()),
        ({"assets": "5", "debts": "NaN"}, ()),
        ({"assets": "5", "debts": True}, ()),
        ({"assets": "5", "debts": None}, ()),
        # Debts are positive amounts; a negative one breaks the contract.
        ({"assets": "5", "debts": "-1"}, ()),
        # Out of range / too long.
        ({"assets": "1" + "0" * 31, "debts": "0"}, ()),
        ({"assets": "1" * 65, "debts": "0"}, ()),
        ({"assets": "0." + "1" * 64, "debts": "0"}, ()),
        # Only one side present: never completed from `total`.
        ({}, ("debts",)),
        ({}, ("assets",)),
        ({"total": 5.0}, ("debts",)),
        ({"total": 5.0}, ("assets",)),
        # As-of problems.
        ({"assets_as_of": "yesterday"}, ()),
        ({"assets_as_of": "2026-13-01"}, ()),
        ({"assets_as_of": "2026-02-30T00:00:00Z"}, ()),
        ({"assets_as_of": "<script>alert(1)</script>"}, ()),
        ({"assets_as_of": 20260410}, ()),
        ({"assets_as_of": True}, ()),
        ({"assets_as_of": ""}, ()),
        ({"assets_as_of": "2026-04-10T00:00:00Z" + "0" * 40}, ()),
        # Overflows when moved to UTC:
        ({"assets_as_of": "0001-01-01T00:00:00+05:00"}, ()),
        ({"debts_as_of": "x", "debts": "5"}, ()),
        ({"assets_as_of": "\u0662\u0660\u0662\u0666-\u0660\u0664-\u0661\u0660"}, ()),
        # An amount needs its date (null only where no item counts).
        ({"assets_as_of": None}, ()),
        ({"debts": "5", "debts_as_of": None}, ()),
    ],
)
def test_malformed_split_rows_are_rejected_row_by_row(overrides, removed) -> None:
    good = fx.split_row(SGD, "7", "3")
    row = {**fx.split_row(USD, "5", "0"), **overrides}
    for key in removed:
        del row[key]
    context = fx.ctx(
        fx.names(("a", "A")),
        {"portfolios": [{"id": "a", "totals": [good, row]}]},
    )
    m = model(context)
    assert figures(context, "a") == {"SGD": ("7", "3", "4")}  # the good row survives
    assert {"code": "skipped", "n": 1} in m["notes"]
    out = json.dumps(m)
    assert "script" not in out and "NaN" not in out


@pytest.mark.parametrize("missing", ["assets_as_of", "debts_as_of", "currency_id"])
def test_split_row_missing_a_required_field_is_rejected(missing: str) -> None:
    row = fx.split_row(USD, "5", "2")
    del row[missing]
    context = fx.ctx(
        fx.names(("a", "A")), {"portfolios": [{"id": "a", "totals": [row]}]}
    )
    assert statuses(context) == ["unreadable"]
    assert {"code": "skipped", "n": 1} in model(context)["notes"]


def test_extra_keys_are_ignored() -> None:
    row = {**fx.split_row(USD, "5", "2"), "note": "x", "total": "not a number"}
    context = fx.ctx(
        fx.names(("a", "A")), {"portfolios": [{"id": "a", "totals": [row]}]}
    )
    assert figures(context, "a") == {"USD": ("5", "2", "3")}


def test_deprecated_total_is_never_used_for_a_split_row() -> None:
    # `total` adds debts in (and is a double); the split row ignores it.
    row = {**fx.split_row(USD, "100", "30"), "total": 123456789.0}
    context = fx.ctx(
        fx.names(("a", "A")), {"portfolios": [{"id": "a", "totals": [row]}]}
    )
    assert figures(context, "a") == {"USD": ("100", "30", "70")}
    assert "123,456,789" not in doc_from(context)


def test_negative_assets_are_shown_as_recorded() -> None:
    context = fx.ctx(fx.names(("a", "A")), fx.totals(a={USD: "-12.5"}))
    assert figures(context, "a") == {"USD": ("-12.5", "0", "-12.5")}
    assert {"code": "negative"} in model(context)["notes"]


def test_negative_zero_strings_normalise() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.totals(a={USD: fx.split_row(USD, "-0", "-0.00", None, None)}),
    )
    assert figures(context, "a") == {"USD": ("0", "0", "0")}
    assert not re.search(r"\| -0(\.0+)? \|", doc_from(context))


def test_split_rows_with_hostile_currency_or_dates_never_reach_a_document() -> None:
    hostile_currency = [
        {"id": "x", "name": "<script>alert(1)</script>", "symbol": "<b>"},
    ]
    row = fx.split_row("x", "5", "1")
    bad_date = fx.split_row(USD, "5", "1", assets_as_of="<img src=x onerror=alert(1)>")
    context = fx.ctx(
        fx.names(("a", "A")),
        {"portfolios": [{"id": "a", "totals": [row, bad_date]}]},
        currencies_read=hostile_currency,
    )
    for fmt in ("markdown", "text", "html"):
        out = doc_from(context, "en", fmt)
        assert "alert(1)" not in out and "<img" not in out and "onerror" not in out
    assert {"code": "unknown_currency", "n": 1} in model(context)["notes"]
    assert {"code": "skipped", "n": 1} in model(context)["notes"]


def test_split_amounts_are_html_escaped_and_columns_stay_aligned() -> None:
    html_doc = doc("family", "en", "html")
    assert html_doc.count("<th ") == html_doc.count("</th>") == 7 + 3
    # Every amount column is right-aligned, every as-of column is not.
    assert html_doc.count('<th scope="col" class="r">') == 3 + 1
    parser = _Collect()
    parser.feed(html_doc)
    parser.close()
    assert parser.depth == 0
    rows = re.findall(r"<tr>(.*?)</tr>", html_doc)
    for row in rows:
        cells = re.findall(r"<t[dh][ >]", row)
        assert len(cells) in (3, 7), row


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scenario", sorted(fx.SCENARIOS))
def test_amount_table_rows_always_have_seven_columns(scenario: str, lang: str) -> None:
    md = doc(scenario, lang, "markdown")
    # The amounts table has 7 columns (8 unescaped pipes), the exposure table 3.
    widths = {_unescaped_pipes(r) for r in _table_rows(md)}
    assert widths <= {8, 4}, widths
    if scenario != "special":  # its portfolio names contain " | " themselves
        text = doc(scenario, lang, "text")
        widths = {len(r.split(" | ")) for r in text.splitlines() if " | " in r}
        assert widths <= {7, 3}, widths


# ── Older hosts: only the combined `total` (the 1.0.0 behaviour) ───────────
#
# `legacy_totals` rows have no `assets`/`debts`: the combined total adds debts to
# assets, so `read:asset_count` decides whether it is shown (as total assets,
# date unknown) or withheld.


def test_legacy_portfolio_with_liabilities_shows_no_amount() -> None:
    context = fx.ctx(
        fx.names(("home", "Home"), ("cash", "Cash")),
        fx.legacy_totals(home={SGD: 1_800_000.0}, cash={SGD: 50_000.0}),
        fx.counts(home=(1, 1), cash=(2, 0)),
    )
    m = model(context)
    assert statuses(context) == ["liabilities", "shown"]
    home = m["portfolios"][0]
    assert home["totals"] == [] and home["currencies"] == ["SGD"]
    assert home["withheld"] == {"reason": "liabilities", "currencies": ["SGD"]}
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
            "total_liabilities": "0",
            "net_worth": "50000.0",
            "portfolio_count": 1,
            "portfolios": [{"id": "cash", "name": "Cash"}],
        }
    ]
    assert {"code": "liabilities", "n": 1} in m["notes"]
    assert {"code": "legacy", "n": 1} in m["notes"]
    en = doc_from(context)
    assert "| Home | SGD | Not shown (includes liabilities) | — | — | — | — |" in en
    # Shown as total assets; their date is not provided, liabilities are none.
    assert (
        "| Cash | SGD | 50,000.00 | Not provided | 0.00 | No items | 50,000.00 |" in en
    )
    assert "older HelloHQ version" in en
    zh = doc_from(context, "zh-Hans")
    assert "| Home | SGD | 未显示（含负债） | — | — | — | — |" in zh
    assert "| Cash | SGD | 50,000.00 | 未提供 | 0.00 | 无项目 | 50,000.00 |" in zh


def test_legacy_without_item_counts_no_amount_is_shown() -> None:
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.legacy_totals(a={USD: 10}, b={USD: 20}),
        counts_read=False,
    )
    m = model(context)
    assert statuses(context) == ["unclassified", "unclassified"]
    assert m["data_status"] == "withheld" and m["combined_totals"] == []
    assert {"code": "unclassified", "n": 2} in m["notes"]
    en = doc_from(context)
    assert plugin.STRINGS["en"]["sum_withheld"] in en
    assert not re.search(r"\d+\.\d\d", en.split("## Notes")[0])
    assert "| A | USD | Not shown (assets and liabilities unknown) |" in en


def test_legacy_counts_for_some_portfolios_only() -> None:
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.legacy_totals(a={USD: 10}, b={USD: 20}),
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
def test_legacy_malformed_counts_are_treated_as_unknown(entry) -> None:
    context = fx.ctx(
        fx.names(("a", "A")), fx.legacy_totals(a={USD: 1}), {"portfolios": [entry]}
    )
    assert statuses(context) == ["unclassified"]


def test_legacy_counts_read_may_be_a_bare_list() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.legacy_totals(a={USD: 1}),
        [
            {"asset_items": 1, "debt_items": 0},
            "junk",
            {"id": "a", "asset_items": 1, "debt_items": 0},
        ],
    )
    assert statuses(context) == ["shown"]


def test_all_legacy_portfolios_with_liabilities() -> None:
    m = generate("legacy_withheld")["model"]
    assert m["data_status"] == "withheld"
    assert m["summary"]["portfolios_with_totals"] == 0
    assert m["summary"]["portfolios_withheld"] == 2
    for lang in LANGS:
        out = doc("legacy_withheld", lang, "markdown")
        assert plugin.STRINGS[lang]["sum_withheld"] in out
        assert "900,000" not in out and "30,000" not in out


def test_legacy_family_matches_the_1_0_0_behaviour() -> None:
    m = model(fx.LEGACY)
    assert m["data_status"] == "partial"
    assert statuses(fx.LEGACY) == [
        "liabilities", "shown", "unclassified", "shown", "empty",
    ]  # fmt: skip
    assert m["summary"] == {
        "portfolio_count": 5,
        "portfolios_with_totals": 2,
        "portfolios_withheld": 2,
        "currency_count": 4,
    }
    en = doc("legacy", "en", "markdown")
    # Withheld amounts stay out of every figure.
    assert "1,390,000" not in en and "264,840" not in en
    assert "1,250,000" not in en and "48,000" not in en
    assert (
        "- SGD: total assets 140,000.00; total liabilities 0.00; "
        "net worth 140,000.00. "
        "Sum of 2 portfolios (Investments, Cash Savings)." in en
    )


def test_legacy_amounts_beyond_double_precision_are_flagged() -> None:
    # 2**53 minor units is where a JSON double stops holding every cent.
    limit_usd = Decimal(2**53) / 100

    def legacy(currency: str, value: float):
        return fx.ctx(
            fx.names(("a", "A")),
            fx.legacy_totals(a={currency: value}),
            fx.counts(a=(1, 0)),
        )

    below = legacy(USD, int(limit_usd) - 1)
    above = legacy(USD, int(limit_usd) + 1)
    assert {"code": "precision"} not in model(below)["notes"]
    assert {"code": "precision"} in model(above)["notes"]
    # JPY has no minor unit, so the same number is still exact.
    assert {"code": "precision"} not in model(legacy(JPY, int(limit_usd) + 1))["notes"]
    assert "last digits may be inexact" in doc_from(above)
    # Negative amounts count by magnitude.
    assert {"code": "precision"} in model(legacy(USD, -(int(limit_usd) + 1)))["notes"]


def test_legacy_rows_of_one_currency_under_two_ids_are_summed_exactly() -> None:
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


def test_legacy_negative_totals_are_shown_as_recorded() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        fx.legacy_totals(a={SGD: -350000.75}),
        fx.counts(a=(1, 0)),
    )
    assert figures(context, "a") == {"SGD": ("-350000.75", "0", "-350000.75")}
    assert {"code": "negative"} in model(context)["notes"]


def test_a_host_may_mix_shapes_across_portfolios_and_currencies() -> None:
    # First portfolio: current shape. Second: an old-style row beside a split
    # row of ANOTHER currency. Third: both shapes for the SAME currency.
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B"), ("c", "C")),
        {
            "portfolios": [
                {"id": "a", "totals": [fx.split_row(USD, "10", "4")]},
                {
                    "id": "b",
                    "totals": [
                        fx.split_row(USD, "20", "0"),
                        {"currency_id": SGD, "total": 77.0},
                    ],
                },
                {
                    "id": "c",
                    "totals": [
                        fx.split_row(USD, "30", "0"),
                        {"currency_id": USD, "total": 999.0},
                    ],
                },
            ]
        },
        fx.counts(b=(1, 1), c=(1, 0)),
    )
    m = model(context)
    # b: the split USD row is shown, the old-style SGD row is withheld (it has
    # liabilities per the counts); c: the split row wins, the old row is dropped.
    assert figures(context, "a") == {"USD": ("10", "4", "6")}
    assert figures(context, "b") == {"USD": ("20", "0", "20")}
    assert figures(context, "c") == {"USD": ("30", "0", "30")}
    pb = next(p for p in m["portfolios"] if p["id"] == "b")
    assert pb["withheld"] == {"reason": "liabilities", "currencies": ["SGD"]}
    assert pb["totals_status"] == "shown" and pb["currencies"] == ["SGD", "USD"]
    assert {"code": "skipped", "n": 1} in m["notes"]
    assert m["data_status"] == "partial"
    assert "999" not in doc_from(context) and "77.00" not in doc_from(context)
    en = doc_from(context)
    assert "| B | SGD | Not shown (includes liabilities) | — | — | — | — |" in en


def test_a_mixed_portfolio_can_show_a_legacy_row_when_debt_free() -> None:
    context = fx.ctx(
        fx.names(("b", "B")),
        {
            "portfolios": [
                {
                    "id": "b",
                    "totals": [
                        fx.split_row(USD, "20", "0"),
                        {"currency_id": SGD, "total": 77.0},
                    ],
                }
            ]
        },
        fx.counts(b=(2, 0)),
    )
    assert figures(context, "b") == {
        "USD": ("20", "0", "20"),
        "SGD": ("77.0", "0", "77.0"),
    }
    assert model(context)["data_status"] == "complete"


# ── Report model ────────────────────────────────────────────────────────────


def test_model_is_language_neutral_and_json_clean() -> None:
    m = model(fx.FAMILY)
    assert json.loads(json.dumps(m)) == m
    assert m["schema"] == "hellohq.report-pack/3"
    assert m["generated_at"] == "2026-10-04T12:00:30Z"
    assert m["data_status"] == "complete"
    assert m["summary"] == {
        "portfolio_count": 5,
        "portfolios_with_totals": 4,
        "portfolios_withheld": 0,
        "currency_count": 4,
    }
    assert [e["currency"] for e in m["currency_exposure"]] == [
        "CNY",
        "JPY",
        "SGD",
        "USD",
    ]
    assert statuses(fx.FAMILY) == ["shown", "shown", "shown", "shown", "empty"]


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
    assert figures(fx.NEGATIVE, "ptf_c") == {"SGD": ("400000", "650000.5", "-250000.5")}
    en = doc("negative", "en", "markdown")
    assert "-350,000.75" in en  # a negative asset total
    assert "-250,000.50" in en  # liabilities above assets
    # Same currency only: -350000.75 + 20000 + 400000 assets, 650000.5 liabilities.
    assert (
        "- SGD: total assets 69,999.25; total liabilities 650,000.50; "
        "net worth -580,001.25." in en
    )
    assert "-0.00" not in en  # -0.004 USD rounds to zero, printed without a sign
    assert "| Cash | USD | -0.00" not in en
    assert "| Cash | USD | 0.00 | No items | 0.00 | No items | 0.00 |" in en or (
        "| Cash | USD | 0.00 |" in en
    )
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


def test_bad_legacy_total_rows_are_skipped_and_counted() -> None:
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
    assert m["combined_totals"][0]["net_worth"] == str(big + 1)
    assert "100,000,000,000,000,000,000,000,000,001.00" in doc_from(context)


def test_combined_total_is_rounded_once_from_the_exact_sum() -> None:
    # 0.005 + 0.005: each row rounds (half-even) to 0.00, the sum is 0.01.
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")), fx.totals(a={USD: 0.005}, b={USD: 0.005})
    )
    out = doc_from(context)
    assert "| A | USD | 0.00 |" in out and "| B | USD | 0.00 |" in out
    assert (
        "- USD: total assets 0.01; total liabilities 0.00; net worth 0.01. "
        "Sum of 2 portfolios (A, B)." in out
    )
    assert plugin.STRINGS["en"]["note_rounding"] in out


# ── Provenance, currency, honesty ───────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGS)
def test_every_figure_carries_portfolio_currency_as_of_and_read_time(lang: str) -> None:
    md = doc("family", lang, "markdown")
    date = r"\d{4}-\d\d-\d\d"
    rows = [
        line
        for line in md.splitlines()
        if re.match(rf"\| .* \| [A-Z]{{3}} \| [-\d,.]+ \| {date} \| [-\d,.]+ \|", line)
    ]
    assert len(rows) == 8  # Home x2, Investments x3, Chen & Sons, Cash Savings x2
    asof_none = plugin.STRINGS[lang]["asof_none"]
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split(" | ")]
        assert len(cells) == 7
        assert re.fullmatch(date, cells[3])  # the assets' date, from the host
        assert re.fullmatch(date, cells[5]) or cells[5] == asof_none
    # The table's introduction carries the read time, which is not a value date.
    assert "2026-10-04 12:00 UTC" in md.split("|")[0]
    # The combined (summary) figures name the currency and the read time too.
    combined = [line for line in md.splitlines() if re.match(r"- [A-Z]{3}[:：]", line)]
    assert len(combined) == 4
    for line in combined:
        assert "2026-10-04 12:00 UTC" in line


def test_summary_figures_list_their_source_portfolios() -> None:
    md = doc("family", "en", "markdown")
    assert (
        "- SGD: total assets 1,390,000.00; total liabilities 780,000.50; "
        "net worth 609,999.50. Sum of 3 portfolios (Family Home, Investments, "
        "Cash Savings). Read at 2026-10-04 12:00 UTC." in md
    )
    assert (
        "- JPY: total assets 3,500,000; total liabilities 0; net worth 3,500,000. "
        "1 portfolio (Investments). Read at 2026-10-04 12:00 UTC." in md
    )
    assert (
        "- USD: total assets 264,840.38; total liabilities 12,500.75; "
        "net worth 252,339.63." in md
    )
    # No cross-currency grand total anywhere.
    assert md.count("- SGD:") == md.count("- USD:") == 1


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
    assert out["lang"] == "zh-Hans" and out["data_status"] == "complete"
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
