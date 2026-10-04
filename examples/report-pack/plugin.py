"""Report Pack — Tier-1 Python sidecar (compute half of a WebView plugin)
=========================================================================
Turns the portfolio names and aggregated totals the host exposes into a short
"family meeting" report in English or Simplified Chinese (``en`` / ``zh-Hans``).
The WebView half (``ui/``) previews the report and saves it through
``write:external_output`` (the OS save dialog) — a Tier-1 sidecar has no write
path of its own, so the save is always a UI action.

The compute is split in three pure steps, each unit-tested:

1. ``build_report(context, now)``  -> a language-neutral *report model*
   (plain JSON: amounts are decimal strings, notes are codes).
2. ``build_blocks(model, lang)``   -> a small document IR (headings,
   paragraphs, bullet lists, tables) in the chosen language.
3. ``render_markdown`` / ``render_text`` / ``render_html``  -> the three file
   formats the person can save.

Rules this plugin follows (HelloHQ plugin roadmap §5.4)
-------------------------------------------------------
* Information, never advice: no recommendation language; a plain disclaimer is
  always included.
* Provenance on every figure: the portfolio it belongs to and an as-of time.
  The host does not expose a per-value date, so "as of" is the moment this
  report read the workspace (UTC) and the report says so.
* Currency is always shown explicitly. Amounts in different currencies are
  never added together or converted.
* Honest totals: nothing is estimated. A missing total is reported as missing.
* No network, no AI.

Sidecar invocation protocol
---------------------------
The host always calls ``run`` with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``.
``context`` holds the pre-fetched, permission-gated reads keyed by permission id
(denied reads are absent); the real shapes are::

    "read:portfolio_names":   [{"id": "...", "name": "..."}, ...]
    "read:aggregated_values": {"portfolios": [{"id": "...",
                               "totals": [{"currency_id": "usd", "total": 1.5}]}]}

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``report`` (and ``run``) with ``{"lang": "en" | "zh-Hans"}`` ->
  ``{"lang", "generated_at", "data_status", "model", "documents"}``

Permissions required:
  - read:portfolio_names      (portfolio names)
  - read:aggregated_values    (per-portfolio, per-currency totals)
  - write:external_output     (used by the UI half to save the report)
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve
from hellohq_plugin_sdk.protocol import ERR_INVALID_INPUT

SCHEMA = "hellohq.report-pack/1"
DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "zh-Hans")

#: Longest portfolio name shown in a rendered document (the model keeps it all).
MAX_NAME_CHARS = 80
#: Portfolio names listed inline before "and N more".
MAX_INLINE_NAMES = 5
#: Amounts at or above 10**30 are treated as unreadable rather than rendered.
_MAX_ADJUSTED_EXPONENT = 30

# Currencies whose usual number of decimal places is not two (ISO 4217).
_ZERO_DECIMAL = frozenset(
    {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG"}
    | {"RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"}
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

# Decimal arithmetic context: wide enough that summing and rounding amounts of
# up to 10**30 is exact (the default 28 digits would round silently).
_DECIMAL_CONTEXT = Context(prec=80)

_EM_DASH = "—"
_ELLIPSIS = "…"
# Bidirectional override / isolate controls: strip so a name cannot reorder the
# text around it.
_BIDI_CONTROLS = dict.fromkeys(
    [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C]
)

# ─────────────────────────────────────────────────────────────────────────────
# String table (en, zh-Hans). Every key exists in both languages; a unit test
# keeps the key sets and the {placeholders} identical.
#
# Terminology (zh-Hans): 净资产 net worth, 总资产 total assets, 负债 liabilities,
# 币种 currency, 截至 as of, 投资组合 portfolio (the term the app itself uses).
# Keys ending in _one/_other are the singular/plural forms (English only varies).
# ─────────────────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "doc_title": "Family Meeting Report",
        "doc_subtitle": "Portfolio totals recorded in HelloHQ",
        "prepared": (
            "Prepared {asof} UTC. Source: your HelloHQ workspace "
            "(portfolio names and aggregated totals)."
        ),
        "h_summary": "Summary",
        "sum_portfolios": "Portfolios included: {n}.",
        "sum_with_totals": "Portfolios with a total available: {n} of {m}.",
        "sum_currencies": "Currencies in the recorded totals: {n}.",
        "sum_combined_intro": (
            "Combined recorded total by currency (amounts in different "
            "currencies are never added together or converted):"
        ),
        "sum_combined_item_one": (
            "{currency} {amount}: {count} portfolio ({names}). As of {asof} UTC."
        ),
        "sum_combined_item_other": (
            "{currency} {amount}: sum of {count} portfolios ({names}). "
            "As of {asof} UTC."
        ),
        "sum_no_portfolios": (
            "No portfolios were available to this plugin. The workspace may be "
            "empty, or access to portfolio names was not granted."
        ),
        "sum_no_totals": (
            "No totals were available, so no figures are shown. Nothing has "
            "been estimated."
        ),
        "h_portfolios": "Totals by portfolio",
        "portfolios_intro": (
            "One row per portfolio and currency. Source: HelloHQ workspace. "
            "“As of” is the time this report read your workspace."
        ),
        "portfolios_none": "There are no portfolios to list.",
        "th_portfolio": "Portfolio",
        "th_currency": "Currency",
        "th_total": "Recorded total",
        "th_asof": "As of (UTC)",
        "not_available": "Not available",
        "no_values": "No recorded values",
        "h_exposure": "Currency exposure",
        "exposure_intro": (
            "Which currencies appear in the recorded totals and which "
            "portfolios hold them. Amounts are not converted, so no percentage "
            "split between currencies is given."
        ),
        "exposure_none": "No currency information is available.",
        "th_exp_currency": "Currency",
        "th_exp_count": "Portfolios",
        "th_exp_names": "Portfolio names",
        "h_notes": "Notes and disclaimer",
        "note_basis": (
            "Each total is the sum of the latest recorded values of the items "
            "in a portfolio, as supplied by HelloHQ. HelloHQ does not currently "
            "supply a split into total assets and liabilities, so a total is "
            "not necessarily net worth."
        ),
        "note_asof": (
            "“As of” is the time this report read your workspace "
            "(UTC). Each recorded value inside a total has its own date, which "
            "is not available to this plugin."
        ),
        "note_currency": (
            "Currencies are shown as recorded. Totals in different currencies "
            "are not added together or converted."
        ),
        "note_rounding": (
            "Amounts are rounded to the usual number of decimal places of each "
            "currency for display."
        ),
        "note_totals_not_provided": (
            "Aggregated totals were not provided to this plugin (permission not "
            "granted or restricted by policy)."
        ),
        "note_missing_totals_one": (
            "A total is not available for 1 portfolio. Nothing has been "
            "estimated or filled in."
        ),
        "note_missing_totals_other": (
            "Totals are not available for {n} portfolios. Nothing has been "
            "estimated or filled in."
        ),
        "note_negative": ("One or more totals are negative and are shown as recorded."),
        "note_skipped_one": "1 total entry could not be read and was left out.",
        "note_skipped_other": (
            "{n} total entries could not be read and were left out."
        ),
        "note_names_unavailable": (
            "Portfolio names were not available; portfolios are identified by "
            "their internal ID."
        ),
        "note_truncated": "Long portfolio names are shortened in this report.",
        "disclaimer": (
            "Disclaimer: this report is for information only. It is not "
            "financial, investment, tax or legal advice and does not recommend "
            "any action. Figures are the values recorded in your HelloHQ "
            "workspace and have not been independently verified."
        ),
        "unnamed": "(unnamed portfolio)",
        "more_names": "and {n} more",
        "name_sep": ", ",
    },
    "zh-Hans": {
        "doc_title": "家庭会议报告",
        "doc_subtitle": "HelloHQ 中记录的投资组合合计",
        "prepared": (
            "编制时间：{asof} UTC。数据来源：您的 HelloHQ 工作区"
            "（投资组合名称与汇总合计）。"
        ),
        "h_summary": "摘要",
        "sum_portfolios": "纳入的投资组合：{n} 个。",
        "sum_with_totals": "有合计数据的投资组合：{n} 个（共 {m} 个）。",
        "sum_currencies": "记录合计涉及的币种：{n} 种。",
        "sum_combined_intro": "按币种汇总的记录合计（不同币种的金额不会相加，也不做换算）：",
        "sum_combined_item_one": (
            "{currency} {amount}：{count} 个投资组合（{names}）。截至 {asof} UTC。"
        ),
        "sum_combined_item_other": (
            "{currency} {amount}：{count} 个投资组合之和（{names}）。截至 {asof} UTC。"
        ),
        "sum_no_portfolios": (
            "本插件未获取到任何投资组合。工作区可能为空，或未授予读取投资组合名称的权限。"
        ),
        "sum_no_totals": "没有可用的合计数据，因此不显示任何数字，也未作任何估算。",
        "h_portfolios": "各投资组合合计",
        "portfolios_intro": (
            "每个投资组合、每种币种一行。数据来源：HelloHQ 工作区。"
            "“截至”为本报告读取您工作区数据的时间。"
        ),
        "portfolios_none": "没有可列出的投资组合。",
        "th_portfolio": "投资组合",
        "th_currency": "币种",
        "th_total": "记录合计",
        "th_asof": "截至（UTC）",
        "not_available": "暂无数据",
        "no_values": "无记录值",
        "h_exposure": "币种分布",
        "exposure_intro": (
            "记录合计中出现的币种，以及持有这些币种的投资组合。金额未经换算，"
            "因此不提供各币种之间的占比。"
        ),
        "exposure_none": "没有可用的币种信息。",
        "th_exp_currency": "币种",
        "th_exp_count": "投资组合数",
        "th_exp_names": "投资组合名称",
        "h_notes": "说明与免责声明",
        "note_basis": (
            "各合计为 HelloHQ 提供的、投资组合内各项目最近一次记录值之和。"
            "HelloHQ 目前不提供总资产与负债的拆分，因此该合计不一定等于净资产。"
        ),
        "note_asof": (
            "“截至”为本报告读取您工作区数据的时间（UTC）。"
            "合计内各项记录值有各自的记录日期，本插件无法获取。"
        ),
        "note_currency": "币种按记录显示；不同币种的合计不会相加，也不做换算。",
        "note_rounding": "金额按各币种通常的小数位数舍入后显示。",
        "note_totals_not_provided": (
            "本插件未获得汇总合计数据（未授予权限或受策略限制）。"
        ),
        "note_missing_totals_one": "有 1 个投资组合暂无合计数据，未作任何估算或填补。",
        "note_missing_totals_other": (
            "有 {n} 个投资组合暂无合计数据，未作任何估算或填补。"
        ),
        "note_negative": "有一个或多个合计为负数，按记录原样显示。",
        "note_skipped_one": "有 1 条合计数据无法读取，已排除。",
        "note_skipped_other": "有 {n} 条合计数据无法读取，已排除。",
        "note_names_unavailable": "未能获取投资组合名称，以内部编号标识投资组合。",
        "note_truncated": "过长的投资组合名称已在本报告中缩短显示。",
        "disclaimer": (
            "免责声明：本报告仅供信息参考，不构成财务、投资、税务或法律建议，"
            "也不推荐任何操作。数字为您在 HelloHQ 工作区中记录的数值，未经独立核实。"
        ),
        "unnamed": "（未命名投资组合）",
        "more_names": "等另外 {n} 个",
        "name_sep": "、",
    },
}

_LANG_ATTR = {"en": "en", "zh-Hans": "zh-Hans"}


def normalize_lang(value: Any) -> str:
    """Map a requested language tag onto ``en`` or ``zh-Hans``.

    Missing/empty means English. Traditional Chinese (``zh-Hant``/``zh-TW``/
    ``zh-HK``) is *not* silently served as Simplified: it is rejected.
    """
    if value is None or value == "":
        return DEFAULT_LANG
    if not isinstance(value, str):
        raise PluginError("lang must be a string", ERR_INVALID_INPUT)
    tag = value.strip().replace("_", "-").lower()
    if tag == "en" or tag.startswith("en-"):
        return "en"
    if tag in ("zh", "zh-hans", "zh-cn", "zh-sg") or tag.startswith("zh-hans-"):
        return "zh-Hans"
    raise PluginError(
        f"unsupported language: {value!r} (supported: en, zh-Hans)",
        ERR_INVALID_INPUT,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — the language-neutral report model
# ─────────────────────────────────────────────────────────────────────────────


def _clean(text: Any) -> str:
    """Single-line, control-free text safe to put in any output format."""
    out = unicodedata.normalize("NFC", str(text)).translate(_BIDI_CONTROLS)
    kept = []
    for ch in out:
        if ch in "\r\n\t\u2028\u2029\x0b\x0c\x85":
            kept.append(" ")
        elif unicodedata.category(ch) == "Cc":
            continue
        else:
            kept.append(ch)
    return " ".join("".join(kept).split())


def _as_decimal(value: Any) -> Decimal | None:
    """A finite ``Decimal`` from a JSON number or numeric string, else ``None``."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, int):
            dec = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None
            dec = Decimal(repr(value))  # shortest round-trip digits, no float noise
        elif isinstance(value, str):
            dec = Decimal(value.strip())
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite() or dec.adjusted() > _MAX_ADJUSTED_EXPONENT:
        return None
    return Decimal(0) if dec == 0 else dec


def _currency_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = _clean(value).upper()
    return code[:12] if code else None


def _decimal_text(dec: Decimal) -> str:
    return format(dec, "f")


def _parse_names(raw: Any) -> list[dict[str, Any]]:
    """``[{"id","name"}]`` -> ordered, de-duplicated ``[{"id","name"}]``."""
    seen: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict) or entry.get("id") is None:
                continue
            pid = str(entry["id"])
            if pid in seen:
                continue
            name = entry.get("name")
            seen[pid] = {"id": pid, "name": _clean(name) if name is not None else ""}
    return list(seen.values())


def _aggregate_rows(raw: Any) -> list[Any] | None:
    """The ``portfolios`` list of an aggregated-values read, or ``None``."""
    if isinstance(raw, dict) and isinstance(raw.get("portfolios"), list):
        return raw["portfolios"]
    if isinstance(raw, list):
        return raw
    return None


def build_report(context: Any, now: datetime | None = None) -> dict[str, Any]:
    """Build the language-neutral report model from the host context.

    ``now`` is injectable so tests (and goldens) are deterministic; it defaults
    to the current UTC time.
    """
    with localcontext(_DECIMAL_CONTEXT):
        return _build_report(context, now)


def _build_report(context: Any, now: datetime | None) -> dict[str, Any]:
    ctx = context if isinstance(context, dict) else {}
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    generated_at = moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    names_raw = ctx.get("read:portfolio_names")
    names_available = isinstance(names_raw, list)
    agg_rows = _aggregate_rows(ctx.get("read:aggregated_values"))
    totals_provided = agg_rows is not None

    portfolios: list[dict[str, Any]] = _parse_names(names_raw)
    for p in portfolios:
        p["name"] = p["name"] or None
    by_id = {p["id"]: p for p in portfolios}

    skipped = 0
    for p in portfolios:
        p["totals"] = None  # None = no entry from the host; [] = entry, no values
    for entry in agg_rows or []:
        if not isinstance(entry, dict) or entry.get("id") is None:
            skipped += 1
            continue
        pid = str(entry["id"])
        if pid not in by_id:
            # Totals for a portfolio whose name was not provided.
            by_id[pid] = {"id": pid, "name": None, "totals": None}
            portfolios.append(by_id[pid])
        if by_id[pid]["totals"] is not None:
            skipped += 1  # a second entry for the same portfolio: keep the first
            continue
        sums: dict[str, Decimal] = {}
        for row in entry.get("totals") or []:
            code = (
                _currency_code(row.get("currency_id"))
                if isinstance(row, dict)
                else None
            )
            amount = _as_decimal(row.get("total")) if isinstance(row, dict) else None
            if code is None or amount is None:
                skipped += 1
                continue
            sums[code] = sums.get(code, Decimal(0)) + amount
        by_id[pid]["totals"] = [
            {"currency": c, "amount": _decimal_text(sums[c])} for c in sorted(sums)
        ]

    exposure: dict[str, dict[str, Any]] = {}
    missing = 0
    any_negative = False
    for p in portfolios:
        totals = p.pop("totals")
        if totals is None:
            p["totals_status"] = "missing"
            p["totals"] = []
            missing += 1
            continue
        p["totals_status"] = "ok" if totals else "empty"
        p["totals"] = totals
        for row in totals:
            amount = Decimal(row["amount"])
            any_negative = any_negative or amount < 0
            slot = exposure.setdefault(
                row["currency"],
                {"currency": row["currency"], "sum": Decimal(0), "portfolios": []},
            )
            slot["sum"] += amount
            slot["portfolios"].append({"id": p["id"], "name": p["name"]})

    currency_exposure = [
        {
            "currency": slot["currency"],
            "combined_total": _decimal_text(slot["sum"]),
            "portfolio_count": len(slot["portfolios"]),
            "portfolios": slot["portfolios"],
        }
        for _, slot in sorted(exposure.items())
    ]

    if not portfolios:
        status = "no_portfolios"
    elif not currency_exposure:
        status = "no_totals"
    elif missing:
        status = "partial"
    else:
        status = "complete"

    notes: list[dict[str, Any]] = [
        {"code": "basis"},
        {"code": "asof"},
        {"code": "currency"},
        {"code": "rounding"},
    ]
    if portfolios and not totals_provided:
        notes.append({"code": "totals_not_provided"})
    elif missing:
        notes.append({"code": "missing_totals", "n": missing})
    if any_negative:
        notes.append({"code": "negative"})
    if skipped:
        notes.append({"code": "skipped", "n": skipped})
    if portfolios and not names_available:
        notes.append({"code": "names_unavailable"})
    if any(p["name"] and len(p["name"]) > MAX_NAME_CHARS for p in portfolios):
        notes.append({"code": "truncated"})

    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "data_status": status,
        "portfolios": portfolios,
        "summary": {
            "portfolio_count": len(portfolios),
            "portfolios_with_totals": sum(
                1 for p in portfolios if p["totals_status"] == "ok"
            ),
            "currency_count": len(currency_exposure),
        },
        "currency_exposure": currency_exposure,
        "notes": notes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — the document IR
#
# A block is one of
#   ("h1", text) ("h2", text) ("p", text) ("ul", [text, ...])
#   ("table", headers, rows, aligns)   aligns: "l" | "r" per column
# All text is plain; each renderer escapes for its own format.
# ─────────────────────────────────────────────────────────────────────────────

Block = tuple[Any, ...]


def format_amount(amount: str, currency: str) -> str:
    """``"-1234.5"``, ``"USD"`` -> ``"-1,234.50"`` (ASCII minus, grouped)."""
    dec = Decimal(amount)
    if currency in _ZERO_DECIMAL:
        places = 0
    elif currency in _THREE_DECIMAL:
        places = 3
    else:
        places = 2
    with localcontext(_DECIMAL_CONTEXT):
        quantum = Decimal(1).scaleb(-places)
        rounded = dec.quantize(quantum, rounding=ROUND_HALF_EVEN)
        if rounded == 0:
            rounded = Decimal(0).quantize(quantum)  # never print "-0.00"
        return format(rounded, ",f")


def _display_name(portfolio: dict[str, Any], lang: str) -> str:
    name = portfolio.get("name")
    if not name:
        # No name: fall back to the id (names unavailable) or a placeholder.
        return (
            _clean(portfolio["id"]) if portfolio.get("id") else STRINGS[lang]["unnamed"]
        )
    if len(name) > MAX_NAME_CHARS:
        return name[: MAX_NAME_CHARS - 1].rstrip() + _ELLIPSIS
    return name


def _name_list(portfolios: list[dict[str, Any]], lang: str) -> str:
    s = STRINGS[lang]
    shown = [_display_name(p, lang) for p in portfolios[:MAX_INLINE_NAMES]]
    text = s["name_sep"].join(shown)
    rest = len(portfolios) - len(shown)
    if rest > 0:
        text += s["name_sep"] + s["more_names"].format(n=rest)
    return text


def _plural(lang: str, key: str, n: int) -> str:
    table = STRINGS[lang]
    return (
        table[f"{key}_one"]
        if n == 1 and f"{key}_one" in table
        else table[f"{key}_other"]
    )


def _display_time(generated_at: str) -> str:
    """``2026-10-04T12:00:00Z`` -> ``2026-10-04 12:00`` (always UTC)."""
    return generated_at[:16].replace("T", " ")


def build_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    s = STRINGS[lang]
    asof = _display_time(model["generated_at"])
    status = model["data_status"]
    summary = model["summary"]
    portfolios = model["portfolios"]
    exposure = model["currency_exposure"]

    blocks: list[Block] = [
        ("h1", s["doc_title"]),
        ("p", s["doc_subtitle"]),
        ("p", s["prepared"].format(asof=asof)),
    ]

    # ── Summary ───────────────────────────────────────────────────────────────
    blocks.append(("h2", s["h_summary"]))
    if status == "no_portfolios":
        blocks.append(("p", s["sum_no_portfolios"]))
    else:
        items = [
            s["sum_portfolios"].format(n=summary["portfolio_count"]),
            s["sum_with_totals"].format(
                n=summary["portfolios_with_totals"], m=summary["portfolio_count"]
            ),
            s["sum_currencies"].format(n=summary["currency_count"]),
        ]
        blocks.append(("ul", items))
        if status == "no_totals":
            blocks.append(("p", s["sum_no_totals"]))
        else:
            blocks.append(("p", s["sum_combined_intro"]))
            blocks.append(
                (
                    "ul",
                    [
                        _plural(lang, "sum_combined_item", e["portfolio_count"]).format(
                            currency=e["currency"],
                            amount=format_amount(e["combined_total"], e["currency"]),
                            count=e["portfolio_count"],
                            names=_name_list(e["portfolios"], lang),
                            asof=asof,
                        )
                        for e in exposure
                    ],
                )
            )

    # ── Totals by portfolio ───────────────────────────────────────────────────
    blocks.append(("h2", s["h_portfolios"]))
    if not portfolios:
        blocks.append(("p", s["portfolios_none"]))
    else:
        blocks.append(("p", s["portfolios_intro"]))
        rows: list[list[str]] = []
        for p in portfolios:
            name = _display_name(p, lang)
            if p["totals_status"] == "ok":
                for t in p["totals"]:
                    rows.append(
                        [
                            name,
                            t["currency"],
                            format_amount(t["amount"], t["currency"]),
                            asof,
                        ]
                    )
            elif p["totals_status"] == "empty":
                rows.append([name, _EM_DASH, s["no_values"], asof])
            else:
                rows.append([name, _EM_DASH, s["not_available"], _EM_DASH])
        blocks.append(
            (
                "table",
                [s["th_portfolio"], s["th_currency"], s["th_total"], s["th_asof"]],
                rows,
                ["l", "l", "r", "l"],
            )
        )

    # ── Currency exposure ─────────────────────────────────────────────────────
    blocks.append(("h2", s["h_exposure"]))
    if not exposure:
        blocks.append(("p", s["exposure_none"]))
    else:
        blocks.append(("p", s["exposure_intro"]))
        blocks.append(
            (
                "table",
                [s["th_exp_currency"], s["th_exp_count"], s["th_exp_names"]],
                [
                    [
                        e["currency"],
                        str(e["portfolio_count"]),
                        _name_list(e["portfolios"], lang),
                    ]
                    for e in exposure
                ],
                ["l", "r", "l"],
            )
        )

    # ── Notes and disclaimer ──────────────────────────────────────────────────
    blocks.append(("h2", s["h_notes"]))
    note_lines = []
    for note in model["notes"]:
        code = note["code"]
        if "n" in note:
            note_lines.append(
                _plural(lang, f"note_{code}", note["n"]).format(n=note["n"])
            )
        else:
            note_lines.append(s[f"note_{code}"])
    blocks.append(("ul", note_lines))
    blocks.append(("p", s["disclaimer"]))
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — renderers
# ─────────────────────────────────────────────────────────────────────────────

# Markdown-significant characters, plus "&" only where it would start an HTML
# entity and ":" only in "://" (so a name cannot become an autolink).
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|#~]|&(?=#?\w+;)|:(?=//))")


def _md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", _clean(text))


def render_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "h1":
            out.append(f"# {_md(block[1])}")
        elif kind == "h2":
            out.append(f"## {_md(block[1])}")
        elif kind == "p":
            out.append(_md(block[1]))
        elif kind == "ul":
            out.append("\n".join(f"- {_md(i)}" for i in block[1]))
        elif kind == "table":
            _, headers, rows, aligns = block
            sep = ["---:" if a == "r" else "---" for a in aligns]
            lines = [
                "| " + " | ".join(_md(h) for h in headers) + " |",
                "| " + " | ".join(sep) + " |",
            ]
            lines += ["| " + " | ".join(_md(c) for c in row) + " |" for row in rows]
            out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def render_text(blocks: list[Block]) -> str:
    out: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "h1":
            title = _clean(block[1])
            out.append(f"{title}\n{'=' * _width(title)}")
        elif kind == "h2":
            title = _clean(block[1])
            out.append(f"{title}\n{'-' * _width(title)}")
        elif kind == "p":
            out.append(_clean(block[1]))
        elif kind == "ul":
            out.append("\n".join(f"* {_clean(i)}" for i in block[1]))
        elif kind == "table":
            _, headers, rows, _aligns = block
            lines = [" | ".join(_clean(h) for h in headers)]
            lines += [" | ".join(_clean(c) for c in row) for row in rows]
            out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def _width(text: str) -> int:
    """Display columns: East Asian wide characters count double."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


_HTML_STYLE = (
    "body{font-family:system-ui,-apple-system,'Segoe UI','PingFang SC',"
    "'Microsoft YaHei',sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;"
    "line-height:1.5;color:#1c1c1c}"
    "h1{font-size:1.6rem;margin-bottom:.25rem}h2{font-size:1.15rem;margin-top:2rem;"
    "border-bottom:1px solid #ccc;padding-bottom:.25rem}"
    "table{border-collapse:collapse;width:100%;margin:.75rem 0}"
    "th,td{border:1px solid #ccc;padding:.35rem .6rem;text-align:left;vertical-align:top}"
    "th{background:#f3f3f3}td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}"
    "@media print{body{margin:0}}"
)


def render_html(blocks: list[Block], lang: str) -> str:
    esc = html.escape
    title = next((esc(_clean(b[1])) for b in blocks if b[0] == "h1"), "")
    body: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "h1":
            body.append(f"<h1>{esc(_clean(block[1]))}</h1>")
        elif kind == "h2":
            body.append(f"<h2>{esc(_clean(block[1]))}</h2>")
        elif kind == "p":
            body.append(f"<p>{esc(_clean(block[1]))}</p>")
        elif kind == "ul":
            items = "".join(f"<li>{esc(_clean(i))}</li>" for i in block[1])
            body.append(f"<ul>{items}</ul>")
        elif kind == "table":
            _, headers, rows, aligns = block
            cls = [' class="r"' if a == "r" else "" for a in aligns]
            head = "".join(
                f'<th scope="col"{c}>{esc(_clean(h))}</th>'
                for h, c in zip(headers, cls)
            )
            trs = "".join(
                "<tr>"
                + "".join(f"<td{c}>{esc(_clean(v))}</td>" for v, c in zip(row, cls))
                + "</tr>"
                for row in rows
            )
            body.append(
                f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"
            )
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{_LANG_ATTR[lang]}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        f"<title>{title}</title>\n<style>{_HTML_STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────


def generate(
    context: Any, lang: str = DEFAULT_LANG, now: datetime | None = None
) -> dict[str, Any]:
    """Model + all three rendered documents for ``lang``."""
    lang = normalize_lang(lang)
    model = build_report(context, now)
    blocks = build_blocks(model, lang)
    stem = f"hellohq-family-report-{model['generated_at'][:10]}-{lang}"
    return {
        "lang": lang,
        "generated_at": model["generated_at"],
        "data_status": model["data_status"],
        "model": model,
        "documents": {
            "markdown": {
                "filename": f"{stem}.md",
                "mime": "text/markdown; charset=utf-8",
                "content": render_markdown(blocks),
            },
            "text": {
                "filename": f"{stem}.txt",
                "mime": "text/plain; charset=utf-8",
                "content": render_text(blocks),
            },
            "html": {
                "filename": f"{stem}.html",
                "mime": "text/html; charset=utf-8",
                "content": render_html(blocks, lang),
            },
        },
    }


def dispatch(function: str, args: Any):
    # The host always calls "run"; the UI's intended function + args are nested
    # under args["input"] (see the protocol note in the module docstring).
    outer = args if isinstance(args, dict) else {}
    ctx = outer.get("context") or {}
    inner = outer.get("input") if isinstance(outer.get("input"), dict) else {}
    fn = inner.get("function", "report")

    if fn in ("report", "run"):
        call_args = inner.get("args") if isinstance(inner.get("args"), dict) else {}
        return generate(ctx, normalize_lang(call_args.get("lang")))
    raise UnsupportedFunction(str(fn))


if __name__ == "__main__":
    serve(dispatch)
