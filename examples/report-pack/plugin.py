"""Report Pack — Tier-1 Python sidecar (compute half of a WebView plugin)
=========================================================================
Turns what the host lets a plugin read (portfolio names, item counts, the
workspace currency list and per-portfolio totals) into a short "family
meeting" report in English or Simplified Chinese (``en`` / ``zh-Hans``). The
WebView half (``ui/``) previews the report and saves it through
``write:external_output`` (the OS save dialog) — a Tier-1 sidecar has no write
path of its own, so the save is always a UI action.

The compute is split in three pure steps, each unit-tested:

1. ``build_report(context, now)``  -> a language-neutral *report model*
   (plain JSON: amounts are exact decimal strings, notes are codes).
2. ``build_blocks(model, lang)``   -> a small document IR (headings,
   paragraphs, bullet lists, tables) in the chosen language.
3. ``render_markdown`` / ``render_text`` / ``render_html``  -> the three file
   formats the person can save.

What the host's totals are, and why most amounts can be withheld
------------------------------------------------------------------
``read:aggregated_values`` gives one number per portfolio and currency: the sum
of the latest recorded value of *every* item in the portfolio
(``PluginDataAccessObject.readAggregatedValues`` in the app). Liabilities are
stored as positive amounts and the app subtracts them for net worth
(``overview_networth_total_value.dart``), but the plugin total adds them in. A
portfolio holding a home and its mortgage would therefore show home + mortgage
— a figure that overstates wealth. So this report shows an amount only when
``read:asset_count`` says the portfolio has **no** liability items (then the
total is its total assets) and withholds it otherwise, saying why.

Currency ids are not codes: the app keys currencies by a row id (a UUID for
the nine built-in currencies). They are resolved to ISO-style codes the way
the app does (``currency_code_helper.dart``): built-in ids first, then the
workspace currency list (``read:currency_rates`` — names and symbols only; the
rates are never read), then the import-id prefix. An amount whose currency
cannot be identified is not shown.

Rules this plugin follows (HelloHQ plugin roadmap §5.4)
-------------------------------------------------------
* Information, never advice: no recommendation language; a plain disclaimer is
  always included.
* Provenance on every figure: the portfolio it belongs to, its currency and
  the time the workspace was read. The host does not expose a per-value date,
  and the report says so.
* Currency is always shown explicitly. Amounts in different currencies are
  never added together or converted.
* Honest totals: nothing is estimated. A missing or withheld amount says so.
* No network, no AI, no storage.

Sidecar invocation protocol
---------------------------
The host always calls ``run`` with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``.
``context`` holds the pre-fetched, permission-gated reads keyed by permission id
(denied reads are absent); the real shapes are::

    "read:portfolio_names":   [{"id": "...", "name": "..."}, ...]
    "read:asset_count":       {"portfolios": [{"id": "...", "asset_items": 3,
                               "debt_items": 1, "total_items": 4}]}
    "read:currency_rates":    [{"id": "...", "name": "USD", "symbol": "$",
                               "rate": 1000000}]
    "read:aggregated_values": {"portfolios": [{"id": "...",
                               "totals": [{"currency_id": "<row id>",
                                           "total": 1.5}]}]}

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``report`` (and ``run``) with ``{"lang": "en" | "zh-Hans"}`` ->
  ``{"lang", "generated_at", "data_status", "model", "documents"}``

Permissions required:
  - read:portfolio_names      (portfolio names)
  - read:asset_count          (asset vs liability item counts per portfolio)
  - read:currency_rates       (currency codes; the rates are never used)
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

SCHEMA = "hellohq.report-pack/2"
DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "zh-Hans")

NAMES = "read:portfolio_names"
COUNTS = "read:asset_count"
CURRENCIES = "read:currency_rates"
TOTALS = "read:aggregated_values"

#: Longest portfolio name shown in a rendered document (the model keeps it all).
MAX_NAME_CHARS = 80
#: Portfolio names listed inline before "and N more".
MAX_INLINE_NAMES = 5
#: Amounts at or above 10**31 are treated as unreadable rather than rendered.
_MAX_ADJUSTED_EXPONENT = 30
#: The host sends totals as JSON numbers (IEEE doubles). At or above 2**53 minor
#: units a double cannot hold every value, so the last digits may be inexact.
_DOUBLE_EXACT_LIMIT = Decimal(2**53)

# Currencies whose usual number of decimal places is not two (ISO 4217).
_ZERO_DECIMAL = frozenset(
    {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG"}
    | {"RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF"}
)
_THREE_DECIMAL = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})

# The app's built-in currency row ids (hellohq lib/app/utils/constant/
# uuid_for_currency.dart, CurrencyHelper.supportedCurrencies). Stable by design:
# the app seeds every workspace with these ids.
_BUILTIN_CURRENCY_IDS = {
    "90dec588-8241-4d40-870a-3c8259d99c91": "USD",
    "42feb0cf-228e-4d81-beb2-9ff881a0b28d": "CNY",
    "c4ca5196-f391-4f58-9d13-8f2e61bef5cc": "SGD",
    "cd4d109e-bb5d-4509-b70f-68e5ca3b6418": "MYR",
    "fc130635-6be6-4c88-9fc2-b7541c153f97": "GBP",
    "40a7aea5-e6aa-4140-b917-e98a1fca792f": "TWD",
    "a081d7ee-a58f-4af7-a225-9e650baeeb5a": "HKD",
    "73bfdb96-39fc-4e0a-975d-e528b71f0647": "IDR",
    "65edf18a-d6d1-41fe-b429-b0dbeeedb4a8": "JPY",
}
# Rows the app creates on document import (document_import_apply_service.dart).
_IMPORT_CURRENCY_PREFIX = "document-import-currency-"
# A code-shaped currency name or symbol, as currency_code_helper.dart accepts.
_CODE_SHAPED = re.compile(r"[A-Z]{2,8}")
# A bare three-letter id (``usd``) is taken as an ISO code (mock-host, tests).
_ISO_SHAPED_ID = re.compile(r"[A-Za-z]{3}")

# Decimal arithmetic context: wide enough that summing and rounding amounts of
# up to 10**31 is exact (the default 28 digits would round silently).
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
# Terminology (zh-Hans): 总资产 total assets, 负债 liabilities, 净资产 net worth
# (named only to say it is not shown), 币种 currency, 读取时间 read at,
# 投资组合 portfolio (the term the app itself uses). The Chinese copy has not
# been reviewed by a native finance reader yet (see README, known gaps).
# Keys ending in _one/_other are the singular/plural forms (English only varies).
# ─────────────────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "doc_title": "Family Meeting Report",
        "doc_subtitle": "Portfolio figures recorded in HelloHQ",
        "prepared": (
            "Prepared {asof} UTC. Source: your HelloHQ workspace (portfolio "
            "names, item counts, currencies and per-portfolio totals)."
        ),
        "h_summary": "Summary",
        "sum_portfolios": "Portfolios included: {n}.",
        "sum_with_totals": "Portfolios with total assets shown: {n} of {m}.",
        "sum_currencies": "Currencies in the recorded values: {n}.",
        "sum_combined_intro": (
            "Combined total assets by currency, for portfolios with no recorded "
            "liabilities (amounts in different currencies are never added "
            "together or converted):"
        ),
        "sum_combined_item_one": (
            "{currency} {amount}: {count} portfolio ({names}). Read at {asof} UTC."
        ),
        "sum_combined_item_other": (
            "{currency} {amount}: sum of {count} portfolios ({names}). "
            "Read at {asof} UTC."
        ),
        "sum_no_portfolios": (
            "No portfolios were available to this plugin. The workspace may be "
            "empty, or access to portfolio names was not granted."
        ),
        "sum_no_totals": (
            "No totals were available, so no figures are shown. Nothing has "
            "been estimated."
        ),
        "sum_withheld": (
            "No amounts are shown: every portfolio with recorded values "
            "includes liabilities or could not be classified (see the notes). "
            "Nothing has been estimated."
        ),
        "h_portfolios": "Total assets by portfolio",
        "portfolios_intro": (
            "One row per portfolio and currency where an amount is shown. "
            "Source: HelloHQ workspace. “Read at” is the time this report read "
            "your workspace."
        ),
        "portfolios_none": "There are no portfolios to list.",
        "th_portfolio": "Portfolio",
        "th_currency": "Currency",
        "th_total": "Total assets",
        "th_asof": "Read at (UTC)",
        "not_available": "Not available",
        "no_values": "No recorded values",
        "unreadable": "Could not be read",
        "withheld_liabilities": "Not shown (includes liabilities)",
        "withheld_unclassified": "Not shown (assets and liabilities unknown)",
        "h_exposure": "Currency exposure",
        "exposure_intro": (
            "Which currencies the recorded values are in, and which portfolios "
            "have values in each. Amounts are not converted, so no percentage "
            "split between currencies is given."
        ),
        "exposure_none": "No currency information is available.",
        "th_exp_currency": "Currency",
        "th_exp_count": "Portfolios",
        "th_exp_names": "Portfolio names",
        "h_notes": "Notes and disclaimer",
        "note_basis": (
            "HelloHQ gives plugins one total per portfolio and currency: the sum "
            "of the latest recorded value of every item, with assets and "
            "liabilities added together. This report therefore shows amounts "
            "only for portfolios with no liabilities recorded, where that total "
            "is the portfolio's total assets. It does not show net worth."
        ),
        "note_app_differs": (
            "Figures may differ from the totals in the HelloHQ app, which "
            "converts every currency into one and applies its own rules for "
            "which items count."
        ),
        "note_asof": (
            "“Read at” is the time this report read your workspace (UTC). Each "
            "value inside a total was recorded on its own date, which plugins "
            "cannot see, so a total can include values recorded long before "
            "this time."
        ),
        "note_currency": (
            "Currencies are shown as recorded. Amounts in different currencies "
            "are not added together or converted."
        ),
        "note_rounding": (
            "Amounts are rounded half to even to the usual number of decimal "
            "places of each currency for display. Combined totals are added "
            "before rounding, so rounded rows may not add up exactly to them."
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
        "note_liabilities_one": (
            "The amounts of 1 portfolio are not shown because it includes "
            "liabilities. HelloHQ does not yet give plugins assets and "
            "liabilities separately, and the combined figure would overstate "
            "wealth."
        ),
        "note_liabilities_other": (
            "The amounts of {n} portfolios are not shown because they include "
            "liabilities. HelloHQ does not yet give plugins assets and "
            "liabilities separately, and the combined figure would overstate "
            "wealth."
        ),
        "note_unclassified_one": (
            "The amounts of 1 portfolio are not shown because no item counts "
            "were available for it, so this plugin cannot tell whether it "
            "includes liabilities."
        ),
        "note_unclassified_other": (
            "The amounts of {n} portfolios are not shown because no item "
            "counts were available for them, so this plugin cannot tell whether "
            "they include liabilities."
        ),
        "note_negative": "One or more amounts are negative and are shown as recorded.",
        "note_precision": (
            "Some amounts are very large. HelloHQ passes totals to plugins as "
            "floating-point numbers, so their last digits may be inexact."
        ),
        "note_skipped_one": "1 total entry could not be read and was left out.",
        "note_skipped_other": (
            "{n} total entries could not be read and were left out."
        ),
        "note_unknown_currency_one": (
            "1 total is in a currency this plugin could not identify and is not shown."
        ),
        "note_unknown_currency_other": (
            "{n} totals are in currencies this plugin could not identify and "
            "are not shown."
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
        "doc_subtitle": "HelloHQ 中记录的投资组合数据",
        "prepared": (
            "编制时间：{asof} UTC。数据来源：您的 HelloHQ 工作区"
            "（投资组合名称、项目数量、币种及各投资组合合计）。"
        ),
        "h_summary": "摘要",
        "sum_portfolios": "纳入的投资组合：{n} 个。",
        "sum_with_totals": "显示总资产的投资组合：{n} 个（共 {m} 个）。",
        "sum_currencies": "记录数值涉及的币种：{n} 种。",
        "sum_combined_intro": (
            "按币种汇总的总资产，仅含未记录负债的投资组合"
            "（不同币种的金额不会相加，也不做换算）："
        ),
        "sum_combined_item_one": (
            "{currency} {amount}：{count} 个投资组合（{names}）。读取时间 {asof} UTC。"
        ),
        "sum_combined_item_other": (
            "{currency} {amount}：{count} 个投资组合之和（{names}）。"
            "读取时间 {asof} UTC。"
        ),
        "sum_no_portfolios": (
            "本插件未获取到任何投资组合。工作区可能为空，或未授予读取投资组合名称的权限。"
        ),
        "sum_no_totals": "没有可用的合计数据，因此不显示任何数字，也未作任何估算。",
        "sum_withheld": (
            "未显示任何金额：所有有记录数值的投资组合均包含负债或无法分类"
            "（见说明）。未作任何估算。"
        ),
        "h_portfolios": "各投资组合总资产",
        "portfolios_intro": (
            "显示金额的投资组合按币种各占一行。数据来源：HelloHQ 工作区。"
            "“读取时间”为本报告读取您工作区数据的时间。"
        ),
        "portfolios_none": "没有可列出的投资组合。",
        "th_portfolio": "投资组合",
        "th_currency": "币种",
        "th_total": "总资产",
        "th_asof": "读取时间（UTC）",
        "not_available": "暂无数据",
        "no_values": "无记录值",
        "unreadable": "无法读取",
        "withheld_liabilities": "未显示（含负债）",
        "withheld_unclassified": "未显示（无法区分资产与负债）",
        "h_exposure": "币种分布",
        "exposure_intro": (
            "记录数值所用的币种，以及在各币种下有记录数值的投资组合。金额未经换算，"
            "因此不提供各币种之间的占比。"
        ),
        "exposure_none": "没有可用的币种信息。",
        "th_exp_currency": "币种",
        "th_exp_count": "投资组合数",
        "th_exp_names": "投资组合名称",
        "h_notes": "说明与免责声明",
        "note_basis": (
            "HelloHQ 向插件提供的是每个投资组合、每种币种的一个合计："
            "各项目最近一次记录值之和，资产与负债相加在一起。因此，本报告仅对"
            "未记录负债的投资组合显示金额，此时该合计即为该投资组合的总资产。"
            "本报告不显示净资产。"
        ),
        "note_app_differs": (
            "这些数字可能与 HelloHQ 应用中显示的合计不同：应用会将所有币种换算为"
            "同一币种，并按自身规则决定计入哪些项目。"
        ),
        "note_asof": (
            "“读取时间”为本报告读取您工作区数据的时间（UTC）。合计中的每个数值"
            "都有各自的记录日期，插件无法获取，因此合计可能包含远早于该时间记录的数值。"
        ),
        "note_currency": "币种按记录显示；不同币种的金额不会相加，也不做换算。",
        "note_rounding": (
            "金额按各币种通常的小数位数，以“四舍六入五成双”的方式舍入后显示。"
            "按币种汇总的合计在舍入前相加，因此舍入后的各行之和可能与汇总合计略有出入。"
        ),
        "note_totals_not_provided": (
            "本插件未获得汇总合计数据（未授予权限或受策略限制）。"
        ),
        "note_missing_totals_one": "有 1 个投资组合暂无合计数据，未作任何估算或填补。",
        "note_missing_totals_other": (
            "有 {n} 个投资组合暂无合计数据，未作任何估算或填补。"
        ),
        "note_liabilities_one": (
            "有 1 个投资组合包含负债，未显示其金额：HelloHQ 目前尚未向插件分别"
            "提供资产与负债，二者相加的数字会高估财富。"
        ),
        "note_liabilities_other": (
            "有 {n} 个投资组合包含负债，未显示其金额：HelloHQ 目前尚未向插件分别"
            "提供资产与负债，二者相加的数字会高估财富。"
        ),
        "note_unclassified_one": (
            "有 1 个投资组合未显示金额：未获得其项目数量信息，"
            "本插件无法判断其中是否包含负债。"
        ),
        "note_unclassified_other": (
            "有 {n} 个投资组合未显示金额：未获得其项目数量信息，"
            "本插件无法判断其中是否包含负债。"
        ),
        "note_negative": "有一个或多个金额为负数，按记录原样显示。",
        "note_precision": (
            "部分金额非常大。HelloHQ 以浮点数形式将合计传给插件，"
            "此类金额的最后几位可能不精确。"
        ),
        "note_skipped_one": "有 1 条合计数据无法读取，已排除。",
        "note_skipped_other": "有 {n} 条合计数据无法读取，已排除。",
        "note_unknown_currency_one": "有 1 条合计所用的币种本插件无法识别，未予显示。",
        "note_unknown_currency_other": (
            "有 {n} 条合计所用的币种本插件无法识别，未予显示。"
        ),
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
        if ch in "\r\n\t  \x0b\x0c\x85":
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


def _code_shaped(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = _clean(value).upper()
    return code if _CODE_SHAPED.fullmatch(code) else None


def _currency_table(raw: Any) -> dict[str, str]:
    """Row id -> code from the workspace currency list. Rates are never read."""
    table: dict[str, str] = {}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            code = _code_shaped(row.get("name")) or _code_shaped(row.get("symbol"))
            if code:
                table.setdefault(row["id"], code)
    return table


def resolve_currency(currency_id: Any, table: dict[str, str]) -> str | None:
    """The display code for a host ``currency_id``, or ``None`` if unknown.

    Same order as the app's ``currencyCodeFromTableData``: a built-in id, then
    the workspace currency row's code-shaped name or symbol; then the app's
    document-import id prefix; then a bare three-letter id taken as ISO.
    """
    if not isinstance(currency_id, str):
        return None
    cid = currency_id.strip()
    if not cid:
        return None
    if cid.lower() in _BUILTIN_CURRENCY_IDS:
        return _BUILTIN_CURRENCY_IDS[cid.lower()]
    if cid in table:
        return table[cid]
    if cid.startswith(_IMPORT_CURRENCY_PREFIX):
        return _code_shaped(cid[len(_IMPORT_CURRENCY_PREFIX) :])
    if _ISO_SHAPED_ID.fullmatch(cid):
        return cid.upper()
    return None


def _decimal_text(dec: Decimal) -> str:
    return format(dec, "f")


def _decimal_places(currency: str) -> int:
    if currency in _ZERO_DECIMAL:
        return 0
    if currency in _THREE_DECIMAL:
        return 3
    return 2


def _beyond_double(amount: Decimal, currency: str) -> bool:
    """True when a double may not hold this amount to its last minor unit."""
    return abs(amount).scaleb(_decimal_places(currency)) >= _DOUBLE_EXACT_LIMIT


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


def _portfolio_rows(raw: Any) -> list[Any] | None:
    """The ``portfolios`` list of a per-portfolio read, or ``None``."""
    if isinstance(raw, dict) and isinstance(raw.get("portfolios"), list):
        return raw["portfolios"]
    if isinstance(raw, list):
        return raw
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _parse_counts(raw: Any) -> dict[str, tuple[int, int]]:
    """``read:asset_count`` -> portfolio id -> (asset items, liability items)."""
    out: dict[str, tuple[int, int]] = {}
    for entry in _portfolio_rows(raw) or []:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        assets, debts = (
            _count(entry.get("asset_items")),
            _count(entry.get("debt_items")),
        )
        if assets is not None and debts is not None:
            out.setdefault(str(entry["id"]), (assets, debts))
    return out


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

    names_raw = ctx.get(NAMES)
    names_available = isinstance(names_raw, list)
    agg_rows = _portfolio_rows(ctx.get(TOTALS))
    totals_provided = agg_rows is not None
    counts = _parse_counts(ctx.get(COUNTS))
    table = _currency_table(ctx.get(CURRENCIES))

    portfolios: list[dict[str, Any]] = _parse_names(names_raw)
    for p in portfolios:
        p["name"] = p["name"] or None
    by_id = {p["id"]: p for p in portfolios}

    # Per portfolio: None = no entry from the host; else currency -> exact sum.
    sums_by_id: dict[str, dict[str, Decimal] | None] = {
        p["id"]: None for p in portfolios
    }
    dropped_by_id: dict[str, int] = {}
    skipped = 0
    unknown_currency = 0
    for entry in agg_rows or []:
        if not isinstance(entry, dict) or entry.get("id") is None:
            skipped += 1
            continue
        pid = str(entry["id"])
        if pid not in by_id:
            # Totals for a portfolio whose name was not provided.
            by_id[pid] = {"id": pid, "name": None}
            portfolios.append(by_id[pid])
            sums_by_id[pid] = None
        if sums_by_id[pid] is not None:
            skipped += 1  # a second entry for the same portfolio: keep the first
            continue
        sums: dict[str, Decimal] = {}
        dropped = 0
        rows = entry.get("totals")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                skipped += 1
                dropped += 1
                continue
            amount = _as_decimal(row.get("total"))
            if amount is None or not isinstance(row.get("currency_id"), str):
                skipped += 1
                dropped += 1
                continue
            code = resolve_currency(row["currency_id"], table)
            if code is None:
                unknown_currency += 1
                dropped += 1
                continue
            sums[code] = sums.get(code, Decimal(0)) + amount
        sums_by_id[pid] = sums
        dropped_by_id[pid] = dropped

    exposure: dict[str, list[dict[str, Any]]] = {}
    combined: dict[str, dict[str, Any]] = {}
    status_count = {
        s: 0
        for s in (
            "shown",
            "liabilities",
            "unclassified",
            "empty",
            "unreadable",
            "missing",
        )
    }
    any_negative = False
    any_imprecise = False
    for p in portfolios:
        sums = sums_by_id.get(p["id"])
        assets, debts = counts.get(p["id"], (None, None))
        p["asset_items"], p["liability_items"] = assets, debts
        p["currencies"] = sorted(sums or {})
        p["totals"] = []
        if sums is None:
            status = "missing"
        elif not sums:
            status = "unreadable" if dropped_by_id.get(p["id"]) else "empty"
        elif debts is None:
            status = "unclassified"
        elif debts > 0:
            status = "liabilities"
        else:
            status = "shown"
        p["totals_status"] = status
        status_count[status] += 1
        for code in p["currencies"]:
            exposure.setdefault(code, []).append({"id": p["id"], "name": p["name"]})
        if status != "shown":
            continue  # withheld amounts never enter the model
        for code in p["currencies"]:
            amount = sums[code]
            any_negative = any_negative or amount < 0
            any_imprecise = any_imprecise or _beyond_double(amount, code)
            p["totals"].append({"currency": code, "amount": _decimal_text(amount)})
            slot = combined.setdefault(
                code, {"currency": code, "sum": Decimal(0), "portfolios": []}
            )
            slot["sum"] += amount
            slot["portfolios"].append({"id": p["id"], "name": p["name"]})

    combined_totals = [
        {
            "currency": slot["currency"],
            "total_assets": _decimal_text(slot["sum"]),
            "portfolio_count": len(slot["portfolios"]),
            "portfolios": slot["portfolios"],
        }
        for _, slot in sorted(combined.items())
    ]
    currency_exposure = [
        {"currency": code, "portfolio_count": len(holders), "portfolios": holders}
        for code, holders in sorted(exposure.items())
    ]

    shown = status_count["shown"]
    withheld = status_count["liabilities"] + status_count["unclassified"]
    if not portfolios:
        data_status = "no_portfolios"
    elif not shown and not withheld:
        data_status = "no_totals"
    elif not shown:
        data_status = "withheld"
    elif shown + status_count["empty"] == len(portfolios):
        data_status = "complete"
    else:
        data_status = "partial"

    notes: list[dict[str, Any]] = [
        {"code": "basis"},
        {"code": "app_differs"},
        {"code": "asof"},
        {"code": "currency"},
        {"code": "rounding"},
    ]
    if portfolios and not totals_provided:
        notes.append({"code": "totals_not_provided"})
    elif status_count["missing"]:
        notes.append({"code": "missing_totals", "n": status_count["missing"]})
    if status_count["liabilities"]:
        notes.append({"code": "liabilities", "n": status_count["liabilities"]})
    if status_count["unclassified"]:
        notes.append({"code": "unclassified", "n": status_count["unclassified"]})
    if any_negative:
        notes.append({"code": "negative"})
    if any_imprecise:
        notes.append({"code": "precision"})
    if skipped:
        notes.append({"code": "skipped", "n": skipped})
    if unknown_currency:
        notes.append({"code": "unknown_currency", "n": unknown_currency})
    if portfolios and not names_available:
        notes.append({"code": "names_unavailable"})
    if any(p["name"] and len(p["name"]) > MAX_NAME_CHARS for p in portfolios):
        notes.append({"code": "truncated"})

    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "data_status": data_status,
        "portfolios": portfolios,
        "summary": {
            "portfolio_count": len(portfolios),
            "portfolios_with_totals": shown,
            "portfolios_withheld": withheld,
            "currency_count": len(currency_exposure),
        },
        "combined_totals": combined_totals,
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
    places = _decimal_places(currency)
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


_WITHHELD_LABEL = {
    "liabilities": "withheld_liabilities",
    "unclassified": "withheld_unclassified",
}


def build_blocks(model: dict[str, Any], lang: str) -> list[Block]:
    s = STRINGS[lang]
    asof = _display_time(model["generated_at"])
    status = model["data_status"]
    summary = model["summary"]
    portfolios = model["portfolios"]
    combined = model["combined_totals"]
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
        elif status == "withheld":
            blocks.append(("p", s["sum_withheld"]))
        else:
            blocks.append(("p", s["sum_combined_intro"]))
            blocks.append(
                (
                    "ul",
                    [
                        _plural(lang, "sum_combined_item", e["portfolio_count"]).format(
                            currency=e["currency"],
                            amount=format_amount(e["total_assets"], e["currency"]),
                            count=e["portfolio_count"],
                            names=_name_list(e["portfolios"], lang),
                            asof=asof,
                        )
                        for e in combined
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
            st = p["totals_status"]
            if st == "shown":
                for t in p["totals"]:
                    rows.append(
                        [
                            name,
                            t["currency"],
                            format_amount(t["amount"], t["currency"]),
                            asof,
                        ]
                    )
            elif st in _WITHHELD_LABEL:
                currencies = s["name_sep"].join(p["currencies"])
                rows.append([name, currencies, s[_WITHHELD_LABEL[st]], asof])
            elif st == "empty":
                rows.append([name, _EM_DASH, s["no_values"], asof])
            elif st == "unreadable":
                rows.append([name, _EM_DASH, s["unreadable"], asof])
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
