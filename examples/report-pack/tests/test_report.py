"""Unit tests for the report model, both languages, and the three renderers."""

from __future__ import annotations

import os
import re
import string
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

import fixtures as fx
import pytest
from hellohq_plugin_sdk import PluginError

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
    import json

    model = generate("family")["model"]
    check_golden(
        "family.model.json", json.dumps(model, indent=2, ensure_ascii=False) + "\n"
    )


# ── String table ────────────────────────────────────────────────────────────


def _placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_string_tables_have_identical_keys_and_placeholders() -> None:
    en, zh = plugin.STRINGS["en"], plugin.STRINGS["zh-Hans"]
    assert set(en) == set(zh)
    for key in en:
        assert _placeholders(en[key]) == _placeholders(zh[key]), key


def test_zh_uses_consistent_terminology() -> None:
    text = "".join(doc(s, "zh-Hans", "markdown") for s in fx.SCENARIOS)
    for term in ("净资产", "总资产", "负债", "币种", "截至"):
        assert term in text, term
    # Variants this pack deliberately does not use.
    for variant in ("货币", "币别", "资产净值", "总负债", "债务"):
        assert variant not in text, variant


# ── Language selection ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag,expected",
    [
        (None, "en"),
        ("", "en"),
        ("en", "en"),
        ("EN-gb", "en"),
        ("en_US", "en"),
        ("zh", "zh-Hans"),
        ("zh-Hans", "zh-Hans"),
        ("zh_CN", "zh-Hans"),
        ("ZH-SG", "zh-Hans"),
        ("zh-Hans-CN", "zh-Hans"),
    ],
)
def test_normalize_lang(tag, expected) -> None:
    assert plugin.normalize_lang(tag) == expected


@pytest.mark.parametrize("tag", ["zh-Hant", "zh-TW", "zh-HK", "fr", "xx", 5, ["en"]])
def test_unsupported_lang_is_rejected_not_substituted(tag) -> None:
    with pytest.raises(PluginError) as err:
        plugin.normalize_lang(tag)
    assert err.value.code == "invalid_input"


# ── Report model ────────────────────────────────────────────────────────────


def test_model_is_language_neutral_and_json_clean() -> None:
    import json

    en = plugin.build_report(fx.FAMILY, fx.NOW)
    assert json.loads(json.dumps(en)) == en
    assert en["schema"] == "hellohq.report-pack/1"
    assert en["generated_at"] == "2026-10-04T12:00:30Z"
    assert en["data_status"] == "complete"
    assert en["summary"] == {
        "portfolio_count": 3,
        "portfolios_with_totals": 3,
        "currency_count": 4,
    }
    assert [e["currency"] for e in en["currency_exposure"]] == [
        "CNY",
        "JPY",
        "SGD",
        "USD",
    ]


def test_amounts_are_exact_decimal_strings_never_summed_across_currencies() -> None:
    model = plugin.build_report(fx.FAMILY, fx.NOW)
    by_cur = {e["currency"]: e for e in model["currency_exposure"]}
    assert by_cur["SGD"]["combined_total"] == "1348000.0"
    assert by_cur["SGD"]["portfolio_count"] == 2
    assert by_cur["USD"]["combined_total"] == "263340.126"
    # Floats do not leak binary noise into the model.
    summed = plugin.build_report(
        fx.ctx(
            fx.names(("a", "A")),
            fx.totals(a={"usd": 0.1}),
        ),
        fx.NOW,
    )
    two = plugin.build_report(
        fx.ctx(
            fx.names(("a", "A"), ("b", "B")),
            fx.totals(a={"usd": 0.1}, b={"usd": 0.2}),
        ),
        fx.NOW,
    )
    assert summed["currency_exposure"][0]["combined_total"] == "0.1"
    assert two["currency_exposure"][0]["combined_total"] == "0.3"


def test_zero_portfolios() -> None:
    model = plugin.build_report(fx.EMPTY, fx.NOW)
    assert model["data_status"] == "no_portfolios"
    assert model["portfolios"] == [] and model["currency_exposure"] == []
    for lang in LANGS:
        out = doc("empty", lang, "markdown")
        assert plugin.STRINGS[lang]["sum_no_portfolios"] in out
        assert plugin.STRINGS[lang]["portfolios_none"] in out
        assert plugin.STRINGS[lang]["exposure_none"] in out
        assert not re.search(
            r"\d+\.\d\d", out.split(plugin.STRINGS[lang]["h_summary"])[1]
        )


@pytest.mark.parametrize(
    "context", [None, {}, [], "x", 5, {"read:portfolio_names": "bad"}]
)
def test_garbage_context_degrades_to_no_portfolios(context) -> None:
    assert plugin.build_report(context, fx.NOW)["data_status"] == "no_portfolios"


def test_missing_totals_are_reported_missing_not_estimated() -> None:
    model = plugin.build_report(fx.NO_TOTALS, fx.NOW)
    assert model["data_status"] == "no_totals"
    assert [p["totals_status"] for p in model["portfolios"]] == ["missing", "missing"]
    assert {"code": "totals_not_provided"} in model["notes"]
    for lang in LANGS:
        out = doc("no_totals", lang, "markdown")
        s = plugin.STRINGS[lang]
        assert s["sum_no_totals"] in out
        assert s["not_available"] in out
        assert s["exposure_none"] in out
        # No amount-looking figure anywhere in the document.
        assert not re.search(r"\d{1,3}(,\d{3})*\.\d{2,3}", out), out


def test_partial_totals_distinguish_missing_from_empty() -> None:
    model = plugin.build_report(fx.PARTIAL, fx.NOW)
    assert model["data_status"] == "partial"
    assert [p["totals_status"] for p in model["portfolios"]] == [
        "ok",
        "missing",
        "empty",
    ]
    assert {"code": "missing_totals", "n": 1} in model["notes"]
    en = doc("partial", "en", "markdown")
    assert "Not available" in en and "No recorded values" in en
    assert "Totals are not available" not in en  # singular form for exactly one
    assert "A total is not available for 1 portfolio" in en
    assert model["summary"]["portfolios_with_totals"] == 1


def test_negative_totals_are_shown_as_recorded() -> None:
    model = plugin.build_report(fx.NEGATIVE, fx.NOW)
    assert {"code": "negative"} in model["notes"]
    en = doc("negative", "en", "markdown")
    assert "-350,000.75" in en
    assert "SGD -330,000.75" in en  # -350000.75 + 20000.0, same currency only
    assert "-0.00" not in en  # -0.004 USD rounds to zero, printed without a sign
    assert "| USD | 0.00 |" in en
    zh = doc("negative", "zh-Hans", "markdown")
    assert plugin.STRINGS["zh-Hans"]["note_negative"] in zh


def test_negative_total_is_never_called_net_worth_or_advice() -> None:
    # The host total is not split into assets/liabilities, so it must not be
    # presented as 净资产 / net worth: the notes say it is not necessarily so.
    en = doc("negative", "en", "markdown")
    assert "not necessarily net worth" in en
    zh = doc("negative", "zh-Hans", "markdown")
    assert "不一定等于净资产" in zh


def test_aggregated_only_portfolio_and_names_unavailable() -> None:
    context = fx.ctx(None, fx.totals(ptf_x={"eur": 10.0}))
    model = plugin.build_report(context, fx.NOW)
    assert [p["id"] for p in model["portfolios"]] == ["ptf_x"]
    assert model["portfolios"][0]["name"] is None
    assert {"code": "names_unavailable"} in model["notes"]
    out = doc_from(context, "en")
    assert r"| ptf\_x | EUR | 10.00 |" in out


def doc_from(context, lang: str, fmt: str = "markdown") -> str:
    return plugin.generate(context, lang, now=fx.NOW)["documents"][fmt]["content"]


def test_unnamed_portfolio_with_known_names_read() -> None:
    context = fx.ctx(
        [
            {"id": "p1", "name": ""},
            {"id": "p1", "name": "dup"},
            {"name": "no id"},
            "junk",
        ],
        fx.totals(p1={"usd": 1}),
    )
    model = plugin.build_report(context, fx.NOW)
    assert len(model["portfolios"]) == 1  # duplicate id and malformed rows dropped
    assert model["portfolios"][0]["name"] is None


def test_bad_total_rows_are_skipped_and_counted() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {
                    "id": "a",
                    "totals": [
                        {"currency_id": "usd", "total": 5},
                        {"currency_id": "usd", "total": float("nan")},
                        {"currency_id": "eur", "total": float("inf")},
                        {"currency_id": "gbp", "total": True},
                        {"currency_id": "chf", "total": None},
                        {"currency_id": "", "total": 1},
                        {"currency_id": "jpy", "total": "1e999"},
                        "junk",
                    ],
                },
                {"totals": []},
                "junk",
            ]
        },
    )
    model = plugin.build_report(context, fx.NOW)
    assert [(r["currency"], r["amount"]) for r in model["portfolios"][0]["totals"]] == [
        ("USD", "5")
    ]
    assert {"code": "skipped", "n": 9} in model["notes"]


def test_duplicate_aggregate_entries_keep_the_first() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {"id": "a", "totals": [{"currency_id": "usd", "total": 1}]},
                {"id": "a", "totals": [{"currency_id": "usd", "total": 99}]},
            ]
        },
    )
    model = plugin.build_report(context, fx.NOW)
    assert model["portfolios"][0]["totals"] == [{"currency": "USD", "amount": "1"}]
    assert {"code": "skipped", "n": 1} in model["notes"]


def test_repeated_currency_rows_in_one_portfolio_are_summed_exactly() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        {
            "portfolios": [
                {
                    "id": "a",
                    "totals": [
                        {"currency_id": "usd", "total": 0.1},
                        {"currency_id": "USD", "total": 0.2},
                    ],
                }
            ]
        },
    )
    assert plugin.build_report(context, fx.NOW)["portfolios"][0]["totals"] == [
        {"currency": "USD", "amount": "0.3"}
    ]


def test_aggregated_read_may_be_a_bare_list() -> None:
    context = fx.ctx(
        fx.names(("a", "A")),
        [{"id": "a", "totals": [{"currency_id": "usd", "total": 2}]}],
    )
    assert plugin.build_report(context, fx.NOW)["data_status"] == "complete"


def test_now_is_converted_to_utc() -> None:
    from datetime import datetime, timedelta, timezone

    sgt = datetime(2026, 10, 4, 20, 5, 59, 999, tzinfo=timezone(timedelta(hours=8)))
    assert plugin.build_report(fx.EMPTY, sgt)["generated_at"] == "2026-10-04T12:05:59Z"


# ── Number formatting ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        ("1234567.891", "USD", "1,234,567.89"),
        ("-1234.5", "SGD", "-1,234.50"),
        ("0.125", "USD", "0.12"),  # half-even
        ("0.135", "USD", "0.14"),
        ("-0.004", "USD", "0.00"),  # no negative zero
        ("0", "CNY", "0.00"),
        ("3500000", "JPY", "3,500,000"),
        ("2.5", "KRW", "2"),
        ("1.2345", "KWD", "1.234"),
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
        ("12.50", Decimal("12.50")),
        (" 7 ", Decimal(7)),
        (10**30 - 1, Decimal(10**30 - 1)),
        (True, None),
        (None, None),
        ("abc", None),
        ("NaN", None),
        ("Infinity", None),
        (float("nan"), None),
        (float("-inf"), None),
        (10**31, None),
        ([1], None),
    ],
)
def test_as_decimal(value, expected) -> None:
    assert plugin._as_decimal(value) == expected


def test_very_large_sums_are_exact() -> None:
    big = 10**29
    context = fx.ctx(
        fx.names(("a", "A"), ("b", "B")),
        fx.totals(a={"usd": big}, b={"usd": 1}),
    )
    model = plugin.build_report(context, fx.NOW)
    assert model["currency_exposure"][0]["combined_total"] == str(big + 1)
    assert "100,000,000,000,000,000,000,000,000,001.00" in doc_from(context, "en")


# ── Provenance, currency, honesty ───────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGS)
def test_every_figure_carries_portfolio_currency_and_asof(lang: str) -> None:
    md = doc("family", lang, "markdown")
    rows = [
        line
        for line in md.splitlines()
        if re.match(r"\| .* \| [A-Z]{3} \| [-\d,.]+ \|", line)
    ]
    assert len(rows) == 6  # one per portfolio x currency
    for row in rows:
        assert row.rstrip().endswith("| 2026-10-04 12:00 |")
    # The combined (summary) figures name the currency and the as-of time too.
    combined = [
        line for line in md.splitlines() if re.match(r"- [A-Z]{3} [-\d,.]+[:：]", line)
    ]
    assert len(combined) == 4
    for line in combined:
        assert "2026-10-04 12:00 UTC" in line


def test_summary_figures_list_their_source_portfolios() -> None:
    md = doc("family", "en", "markdown")
    assert (
        "- SGD 1,348,000.00: sum of 2 portfolios (Family Home, Investments). As of 2026-10-04 12:00 UTC."
        in md
    )
    assert "- CNY 480,000.50: 2 portfolios" not in md
    assert (
        "- CNY 480,000.50: 1 portfolio (Family Home). As of 2026-10-04 12:00 UTC." in md
    )


def test_more_than_five_names_are_summarised() -> None:
    rows = [(f"p{i}", f"Portfolio {i}") for i in range(8)]
    context = fx.ctx(fx.names(*rows), fx.totals(**{pid: {"usd": 1} for pid, _ in rows}))
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
    for p in plugin.build_report(fx.SCENARIOS[scenario], fx.NOW)["portfolios"]:
        text = text.replace(p["name"] or "", "")
    lowered = text.lower()
    for word in _BANNED[lang]:
        assert word not in lowered, (scenario, lang, word)


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
def test_markdown_escapes_names_and_keeps_tables_intact(lang: str) -> None:
    md = doc("special", lang, "markdown")
    for table in re.split(r"\n\n", md):
        rows = _table_rows(table)
        if rows:
            counts = {_unescaped_pipes(r) for r in rows}
            assert len(counts) == 1, rows  # same column count on every row
    assert r"Savings \| 2026 \*draft\* \[x\](http\://evil) \<b\>bold\</b\>" in md
    assert not re.search(r"(?<!\\)<", md)  # no unescaped angle bracket survives


def test_control_and_bidi_characters_are_stripped() -> None:
    model = plugin.build_report(fx.SPECIAL, fx.NOW)
    cjk = next(p for p in model["portfolios"] if p["id"] == "p3")
    assert cjk["name"] == "家庭基金 evil line2"
    for fmt in ("markdown", "text", "html"):
        out = doc("special", "en", fmt)
        assert not re.search("[\u202a-\u202e\u2066-\u2069\x00-\x08\x0b-\x1f]", out)


def test_very_long_names_are_shortened_in_documents_but_not_in_the_model() -> None:
    model = plugin.build_report(fx.SPECIAL, fx.NOW)
    long_name = next(p for p in model["portfolios"] if p["id"] == "p2")["name"]
    assert long_name == "A" * 120
    assert {"code": "truncated"} in model["notes"]
    en = doc("special", "en", "markdown")
    assert "A" * 120 not in en
    assert "A" * 79 + "…" in en
    assert plugin.STRINGS["en"]["note_truncated"] in en


def test_name_truncation_is_by_character_not_byte() -> None:
    name = "家" * 120
    context = fx.ctx(fx.names(("a", name)), fx.totals(a={"cny": 1}))
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
    assert docs["text"]["filename"].endswith(".txt") and docs["html"][
        "filename"
    ].endswith(".html")
    for d in docs.values():
        # Safe for the host's save bridge (no separators, no "..", <= 255).
        assert not set("/\\\x00") & set(d["filename"]) and ".." not in d["filename"]
        assert len(d["filename"]) <= 255 and d["mime"].endswith("charset=utf-8")


def test_generate_without_now_uses_the_current_time() -> None:
    out = plugin.generate(fx.EMPTY, "en")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out["generated_at"])


def test_control_characters_inside_names_and_currency_codes_are_dropped() -> None:
    context = fx.ctx(
        fx.names(("a", "Ca\x00sh\x07 \x1b[31mred")),
        {
            "portfolios": [
                {
                    "id": "a",
                    "totals": [
                        {"currency_id": " u\x00sd ", "total": 1},
                        {"currency_id": 5, "total": 1},
                        {"currency_id": None, "total": 1},
                    ],
                }
            ]
        },
    )
    model = plugin.build_report(context, fx.NOW)
    assert model["portfolios"][0]["name"] == "Cash [31mred"
    assert [t["currency"] for t in model["portfolios"][0]["totals"]] == ["USD"]
    assert {"code": "skipped", "n": 2} in model["notes"]


def test_dispatch_rejects_unknown_functions_and_tolerates_odd_arguments() -> None:
    from hellohq_plugin_sdk import UnsupportedFunction

    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", {"context": {}, "input": {"function": "nope"}})
    for odd in (None, "x", {}, {"input": "x"}, {"input": {"args": "x"}}):
        assert plugin.dispatch("run", odd)["data_status"] == "no_portfolios"
