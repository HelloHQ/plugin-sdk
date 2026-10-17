"""Report Pack — Tier-1 Python sidecar (compute half of a WebView plugin)
=========================================================================
Turns what the host lets a plugin read (portfolio names, the workspace
currency list and per-portfolio totals of assets and liabilities) into a short
"family meeting" report in English or Simplified Chinese (``en`` / ``zh-Hans``). The
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

What the host's totals are
--------------------------
``read:aggregated_values`` gives, per portfolio and currency, one ``totals`` row
(``PluginDataAccessObject.readAggregatedValues`` in the app)::

    {"currency_id": "<row id>", "assets": "503834.66", "debts": "300000",
     "assets_as_of": "2026-04-10T00:00:00.000Z",
     "debts_as_of": "2026-03-15T00:00:00.000Z", "total": 803834.66}

``assets`` and ``debts`` are exact decimal strings (debts are the positive
amounts they are recorded as, never subtracted from or added to assets); the
``*_as_of`` fields are the latest value date in that total, or null when no
item of that kind counts. The totals already leave out what the app's own
totals leave out. ``total`` is deprecated and inexact (assets + debts as a
double). This report shows total assets, total liabilities and net worth
(assets - debts, computed exactly within ONE currency) with their as-of dates.

Older hosts send only ``currency_id`` and ``total``, which adds debts to
assets. For such a row (no ``assets``/``debts``) the 1.0.0 behaviour is kept:
``read:asset_count`` says whether the portfolio has liability items; the amount
is shown (as total assets, date unknown) only when it has none, and withheld
otherwise or when that cannot be told, saying why.

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
* Provenance on every figure: the portfolio it belongs to, its currency, the
  date of the latest value in it (as given by the host) and the time the
  workspace was read ("read at" is the read time, never the date of a value).
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
                                           "assets": "1.5", "debts": "0",
                                           "assets_as_of": "2026-04-10T00:00:00.000Z",
                                           "debts_as_of": null,
                                           "total": 1.5}]}]}

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``report`` (and ``run``) with ``{"lang": "en" | "zh-Hans"}`` ->
  ``{"lang", "generated_at", "data_status", "model", "documents"}``

Permissions required:
  - read:portfolio_names      (portfolio names)
  - read:asset_count          (only for an older host that sends a combined
                               ``total``: whether a portfolio has liabilities)
  - read:currency_rates       (currency codes; the rates are never used)
  - read:aggregated_values    (per-portfolio, per-currency totals)
  - write:external_output     (used by the UI half to save the report)
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve
from hellohq_plugin_sdk.protocol import ERR_INVALID_INPUT

SCHEMA = "hellohq.report-pack/3"
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
#: An exact amount string longer than this is unreadable: it keeps every sum
#: within the 80-digit decimal context below exact.
_MAX_AMOUNT_CHARS = 64
#: An older host sends the combined ``total`` as a JSON number (an IEEE double).
#: At or above 2**53 minor units a double cannot hold every value, so the last
#: digits may be inexact. Exact ``assets``/``debts`` strings have no such limit.
_DOUBLE_EXACT_LIMIT = Decimal(2**53)
#: An exact amount as the host writes it: plain digits, optional sign and
#: fraction. No exponent, no whitespace, no "NaN"/"Infinity", ASCII digits only.
_PLAIN_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?", re.ASCII)
#: An ISO-8601 date, or a date-time with optional zone, as the host writes it.
_ISO_DATE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?",
    re.ASCII,
)
_MAX_DATE_CHARS = 40

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

#: Marks a field the host did not send at all (as opposed to an explicit null).
_ABSENT = object()

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
# Terminology (zh-Hans): 总资产 total assets, 负债 liabilities, 净资产 net worth,
# 币种 currency, 读取时间 read at, 投资组合 portfolio (the term the app itself
# uses), 截至 as of. The Chinese copy has not been reviewed by a native finance
# reader yet (see README, known gaps).
# Keys ending in _one/_other are the singular/plural forms (English only varies).
# ─────────────────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "doc_title": "Family Meeting Report",
        "doc_subtitle": "Portfolio figures recorded in HelloHQ",
        "prepared": (
            "Prepared {asof} UTC. Source: your HelloHQ workspace (portfolio "
            "names, currencies and per-portfolio totals of assets and "
            "liabilities)."
        ),
        "h_summary": "Summary",
        "sum_portfolios": "Portfolios included: {n}.",
        "sum_with_totals": "Portfolios with amounts shown: {n} of {m}.",
        "sum_currencies": "Currencies in the recorded values: {n}.",
        "sum_combined_intro": (
            "Combined by currency, for the portfolios whose amounts are shown "
            "(amounts in different currencies are never added together or "
            "converted):"
        ),
        "sum_combined_item_one": (
            "{currency}: total assets {assets}; total liabilities "
            "{liabilities}; net worth {net}. {count} portfolio ({names}). "
            "Read at {asof} UTC."
        ),
        "sum_combined_item_other": (
            "{currency}: total assets {assets}; total liabilities "
            "{liabilities}; net worth {net}. Sum of {count} portfolios "
            "({names}). Read at {asof} UTC."
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
            "No amounts are shown: this HelloHQ version gives only combined "
            "totals, and every portfolio with recorded values includes "
            "liabilities or could not be classified (see the notes). Nothing "
            "has been estimated."
        ),
        "h_portfolios": "Assets, liabilities and net worth by portfolio",
        "portfolios_intro": (
            "One row per portfolio and currency where amounts are shown. "
            "Source: HelloHQ workspace. “As of” is the date of the latest value "
            "in that total, as given by HelloHQ. This report read your "
            "workspace at {asof} UTC."
        ),
        "portfolios_none": "There are no portfolios to list.",
        "th_portfolio": "Portfolio",
        "th_currency": "Currency",
        "th_assets": "Total assets",
        "th_liabilities": "Total liabilities",
        "th_net": "Net worth",
        "th_asof": "As of",
        "asof_none": "No items",
        "asof_unknown": "Not provided",
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
            "Each total is the sum of the latest recorded value of every item "
            "HelloHQ counts for that portfolio and currency. Liabilities are "
            "recorded as positive amounts; this report never adds them to "
            "assets."
        ),
        "note_networth": (
            "Net worth is total assets minus total liabilities, worked out "
            "only within one currency. It is never worked out across "
            "currencies, which are not added together or converted."
        ),
        "note_app_differs": (
            "Figures may differ from the totals in the HelloHQ app, which "
            "converts every currency into one."
        ),
        "note_asof": (
            "“As of” is the date of the latest value in that total, as given "
            "by HelloHQ; other values in it may be older. “No items” means no "
            "item of that kind is counted. “Read at” is the time this report "
            "read your workspace (UTC), not the date of any value."
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
            "liabilities and was read from an older HelloHQ version that gives "
            "plugins only a combined total of assets and liabilities, which "
            "would overstate wealth."
        ),
        "note_liabilities_other": (
            "The amounts of {n} portfolios are not shown because they include "
            "liabilities and were read from an older HelloHQ version that gives "
            "plugins only a combined total of assets and liabilities, which "
            "would overstate wealth."
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
        "note_legacy_one": (
            "1 portfolio was read from an older HelloHQ version that gives "
            "plugins only a combined total. It is shown as total assets because "
            "it has no liabilities recorded; its dates are not available."
        ),
        "note_legacy_other": (
            "{n} portfolios were read from an older HelloHQ version that gives "
            "plugins only a combined total. They are shown as total assets "
            "because they have no liabilities recorded; their dates are not "
            "available."
        ),
        "note_negative": "One or more amounts are negative and are shown as recorded.",
        "note_precision": (
            "Some amounts read from an older HelloHQ version are very large. "
            "That version passes totals as floating-point numbers, so their "
            "last digits may be inexact."
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
            "（投资组合名称、币种及各投资组合的资产与负债合计）。"
        ),
        "h_summary": "摘要",
        "sum_portfolios": "纳入的投资组合：{n} 个。",
        "sum_with_totals": "显示金额的投资组合：{n} 个（共 {m} 个）。",
        "sum_currencies": "记录数值涉及的币种：{n} 种。",
        "sum_combined_intro": (
            "按币种汇总，涵盖显示金额的投资组合（不同币种的金额不会相加，也不做换算）："
        ),
        "sum_combined_item_one": (
            "{currency}：总资产 {assets}；负债 {liabilities}；净资产 {net}。"
            "{count} 个投资组合（{names}）。读取时间 {asof} UTC。"
        ),
        "sum_combined_item_other": (
            "{currency}：总资产 {assets}；负债 {liabilities}；净资产 {net}。"
            "{count} 个投资组合之和（{names}）。读取时间 {asof} UTC。"
        ),
        "sum_no_portfolios": (
            "本插件未获取到任何投资组合。工作区可能为空，或未授予读取投资组合名称的权限。"
        ),
        "sum_no_totals": "没有可用的合计数据，因此不显示任何数字，也未作任何估算。",
        "sum_withheld": (
            "未显示任何金额：当前的 HelloHQ 版本只提供合并后的合计，且所有有记录数值的"
            "投资组合均包含负债或无法分类（见说明）。未作任何估算。"
        ),
        "h_portfolios": "各投资组合的资产、负债与净资产",
        "portfolios_intro": (
            "显示金额的投资组合按币种各占一行。数据来源：HelloHQ 工作区。"
            "“截至”为 HelloHQ 提供的该合计中最新数值的日期。"
            "本报告于 {asof} UTC 读取您的工作区数据。"
        ),
        "portfolios_none": "没有可列出的投资组合。",
        "th_portfolio": "投资组合",
        "th_currency": "币种",
        "th_assets": "总资产",
        "th_liabilities": "负债",
        "th_net": "净资产",
        "th_asof": "截至",
        "asof_none": "无项目",
        "asof_unknown": "未提供",
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
            "每个合计是 HelloHQ 计入该投资组合、该币种的所有项目最近一次记录值之和。"
            "负债按正数记录，本报告从不将其与资产相加。"
        ),
        "note_networth": (
            "净资产为总资产减去负债，仅在同一币种内计算；不同币种之间不计算净资产，"
            "也不相加或换算。"
        ),
        "note_app_differs": (
            "这些数字可能与 HelloHQ 应用中显示的合计不同，因为应用会将所有币种换算为"
            "同一币种。"
        ),
        "note_asof": (
            "“截至”为 HelloHQ 提供的该合计中最新数值的日期，其中其他数值的日期可能更早。"
            "“无项目”表示没有计入该类项目。“读取时间”为本报告读取您工作区数据的时间"
            "（UTC），并非任何数值的日期。"
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
            "有 1 个投资组合包含负债，未显示其金额：其数据来自较旧版本的 HelloHQ，"
            "该版本只向插件提供资产与负债相加后的合计，会高估财富。"
        ),
        "note_liabilities_other": (
            "有 {n} 个投资组合包含负债，未显示其金额：其数据来自较旧版本的 HelloHQ，"
            "该版本只向插件提供资产与负债相加后的合计，会高估财富。"
        ),
        "note_unclassified_one": (
            "有 1 个投资组合未显示金额：未获得其项目数量信息，"
            "本插件无法判断其中是否包含负债。"
        ),
        "note_unclassified_other": (
            "有 {n} 个投资组合未显示金额：未获得其项目数量信息，"
            "本插件无法判断其中是否包含负债。"
        ),
        "note_legacy_one": (
            "有 1 个投资组合的数据来自较旧版本的 HelloHQ，该版本只向插件提供合并后的合计。"
            "因其未记录负债，故按总资产显示，日期不可用。"
        ),
        "note_legacy_other": (
            "有 {n} 个投资组合的数据来自较旧版本的 HelloHQ，该版本只向插件提供合并后的合计。"
            "因其未记录负债，故按总资产显示，日期不可用。"
        ),
        "note_negative": "有一个或多个金额为负数，按记录原样显示。",
        "note_precision": (
            "较旧版本的 HelloHQ 以浮点数形式传递合计，其中部分金额非常大，"
            "最后几位可能不精确。"
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


def _exact_amount(value: Any) -> Decimal | None:
    """An exact ``Decimal`` from the host's decimal STRING, else ``None``.

    Only a plain decimal string is accepted: no JSON number (a double is not
    exact), no exponent, no sign other than ``-``, no whitespace, no
    ``NaN``/``Infinity``, ASCII digits only, at most ``_MAX_AMOUNT_CHARS``
    characters and below 10**31.
    """
    if (
        not isinstance(value, str)
        or len(value) > _MAX_AMOUNT_CHARS
        or not _PLAIN_DECIMAL.fullmatch(value)
    ):
        return None
    dec = Decimal(value)
    if dec.adjusted() > _MAX_ADJUSTED_EXPONENT:
        return None
    return Decimal(0) if dec == 0 else dec


def _as_of_date(value: Any) -> tuple[bool, str | None]:
    """``(ok, "YYYY-MM-DD" | None)`` for an as-of field.

    ``None`` is a valid "no items"; an ISO date or date-time (the host writes
    ``2026-04-10T00:00:00.000Z``) gives its UTC calendar date; anything else is
    not ok.
    """
    if value is None:
        return True, None
    if (
        not isinstance(value, str)
        or len(value) > _MAX_DATE_CHARS
        or not _ISO_DATE.fullmatch(value)
    ):
        return False, None
    try:
        if "T" in value:
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is not None:
                moment = moment.astimezone(UTC)
            day = moment.date()
        else:
            day = date.fromisoformat(value)
    except (ValueError, OverflowError):
        return False, None
    return True, day.isoformat()


def _later(a: str | None, b: str | None) -> str | None:
    """The later of two ISO dates; ``None`` means no date."""
    if a is None or b is None:
        return a or b
    return max(a, b)


def _read_total_row(
    row: Any,
) -> tuple[str, str, dict[str, Any]] | None:
    """One ``totals`` row -> ``(kind, currency_id, values)``, or ``None``.

    ``kind`` is ``"split"`` for a row with ``assets``/``debts`` (the current
    host) and ``"legacy"`` for a row with only the combined ``total`` (an older
    host). ``None`` means the row is malformed and must be skipped; nothing from
    it is used. A row with only one of ``assets``/``debts`` is malformed, not
    legacy: it is never completed from ``total``.
    """
    if not isinstance(row, dict) or not isinstance(row.get("currency_id"), str):
        return None
    if "assets" not in row and "debts" not in row:
        total = _as_decimal(row.get("total"))
        if total is None:
            return None
        return "legacy", row["currency_id"], {"total": total}
    assets = _exact_amount(row.get("assets"))
    debts = _exact_amount(row.get("debts"))
    assets_ok, assets_as_of = _as_of_date(row.get("assets_as_of", _ABSENT))
    debts_ok, debts_as_of = _as_of_date(row.get("debts_as_of", _ABSENT))
    if assets is None or debts is None or debts < 0 or not (assets_ok and debts_ok):
        return None
    # A date is missing only where no item counts; an amount needs its date.
    if (assets != 0 and assets_as_of is None) or (debts != 0 and debts_as_of is None):
        return None
    values = {
        "assets": assets,
        "debts": debts,
        "assets_as_of": assets_as_of,
        "debts_as_of": debts_as_of,
    }
    return "split", row["currency_id"], values


def build_report(context: Any, now: datetime | None = None) -> dict[str, Any]:
    """Build the language-neutral report model from the host context.

    ``now`` is injectable so tests (and goldens) are deterministic; it defaults
    to the current UTC time.
    """
    with localcontext(_DECIMAL_CONTEXT):
        return _build_report(context, now)


def _split_slot() -> dict[str, Any]:
    return {
        "assets": Decimal(0),
        "debts": Decimal(0),
        "assets_as_of": None,
        "debts_as_of": None,
    }


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

    # Per portfolio: None = no entry from the host; else {"split": code -> exact
    # assets/debts, "legacy": code -> combined total of an older host}.
    reads: dict[str, dict[str, dict[str, Any]] | None] = {
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
            reads[pid] = None
        if reads[pid] is not None:
            skipped += 1  # a second entry for the same portfolio: keep the first
            continue
        split: dict[str, dict[str, Any]] = {}
        legacy: dict[str, dict[str, Any]] = {}
        dropped = 0
        rows = entry.get("totals")
        for row in rows if isinstance(rows, list) else []:
            parsed = _read_total_row(row)
            if parsed is None:
                skipped += 1
                dropped += 1
                continue
            kind, currency_id, values = parsed
            code = resolve_currency(currency_id, table)
            if code is None:
                unknown_currency += 1
                dropped += 1
                continue
            if kind == "legacy":
                slot = legacy.setdefault(code, {"total": Decimal(0), "rows": 0})
                slot["total"] += values["total"]
                slot["rows"] += 1
                continue
            slot = split.setdefault(code, _split_slot())
            slot["assets"] += values["assets"]
            slot["debts"] += values["debts"]
            slot["assets_as_of"] = _later(slot["assets_as_of"], values["assets_as_of"])
            slot["debts_as_of"] = _later(slot["debts_as_of"], values["debts_as_of"])
        for code in [c for c in legacy if c in split]:
            # The same currency in both shapes: the split is authoritative; the
            # combined total adds debts in and cannot be reconciled with it.
            skipped += legacy[code]["rows"]
            dropped += legacy[code]["rows"]
            del legacy[code]
        reads[pid] = {"split": split, "legacy": legacy}
        dropped_by_id[pid] = dropped

    exposure: dict[str, list[dict[str, Any]]] = {}
    combined: dict[str, dict[str, Any]] = {}
    status_count = dict.fromkeys(
        ("shown", "liabilities", "unclassified", "empty", "unreadable", "missing"), 0
    )
    withheld_count = {"liabilities": 0, "unclassified": 0}
    legacy_shown = 0
    any_negative = False
    any_imprecise = False
    for p in portfolios:
        read = reads.get(p["id"])
        asset_items, debt_items = counts.get(p["id"], (None, None))
        p["asset_items"], p["liability_items"] = asset_items, debt_items
        split = read["split"] if read else {}
        legacy = read["legacy"] if read else {}
        p["currencies"] = sorted({*split, *legacy})
        p["totals"] = []
        p["withheld"] = None
        # Older host: a combined total is assets only when no liability item
        # exists; withhold it when one does or when that cannot be told.
        if legacy:
            if debt_items is None:
                reason = "unclassified"
            elif debt_items > 0:
                reason = "liabilities"
            else:
                reason = None
            if reason:
                p["withheld"] = {"reason": reason, "currencies": sorted(legacy)}
                withheld_count[reason] += 1
        rows: list[dict[str, Any]] = []
        for code in sorted(split):
            slot = split[code]
            rows.append(
                {
                    "currency": code,
                    "source": "split",
                    "assets": slot["assets"],
                    "liabilities": slot["debts"],
                    "assets_as_of": slot["assets_as_of"],
                    "liabilities_as_of": slot["debts_as_of"],
                }
            )
        if legacy and not p["withheld"]:
            legacy_shown += 1
            for code in sorted(legacy):
                total = legacy[code]["total"]
                any_imprecise = any_imprecise or _beyond_double(total, code)
                rows.append(
                    {
                        "currency": code,
                        "source": "legacy",
                        "assets": total,
                        "liabilities": Decimal(0),
                        "assets_as_of": None,
                        "liabilities_as_of": None,
                    }
                )
        rows.sort(key=lambda r: r["currency"])

        if read is None:
            status = "missing"
        elif rows:
            status = "shown"
        elif p["withheld"]:
            status = p["withheld"]["reason"]
        else:
            status = "unreadable" if dropped_by_id.get(p["id"]) else "empty"
        p["totals_status"] = status
        status_count[status] += 1
        for code in p["currencies"]:
            exposure.setdefault(code, []).append({"id": p["id"], "name": p["name"]})
        for r in rows:  # withheld amounts never enter the model
            code = r["currency"]
            net = r["assets"] - r["liabilities"]
            any_negative = any_negative or r["assets"] < 0 or net < 0
            p["totals"].append(
                {
                    "currency": code,
                    "source": r["source"],
                    "assets": _decimal_text(r["assets"]),
                    "liabilities": _decimal_text(r["liabilities"]),
                    "net_worth": _decimal_text(net),
                    "assets_as_of": r["assets_as_of"],
                    "liabilities_as_of": r["liabilities_as_of"],
                }
            )
            slot = combined.setdefault(
                code,
                {
                    "currency": code,
                    "assets": Decimal(0),
                    "liabilities": Decimal(0),
                    "portfolios": [],
                },
            )
            slot["assets"] += r["assets"]
            slot["liabilities"] += r["liabilities"]
            slot["portfolios"].append({"id": p["id"], "name": p["name"]})

    combined_totals = [
        {
            "currency": slot["currency"],
            "total_assets": _decimal_text(slot["assets"]),
            "total_liabilities": _decimal_text(slot["liabilities"]),
            "net_worth": _decimal_text(slot["assets"] - slot["liabilities"]),
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
    withheld = sum(1 for p in portfolios if p["withheld"])
    if not portfolios:
        data_status = "no_portfolios"
    elif not shown and not withheld:
        data_status = "no_totals"
    elif not shown:
        data_status = "withheld"
    elif not withheld and shown + status_count["empty"] == len(portfolios):
        data_status = "complete"
    else:
        data_status = "partial"

    notes: list[dict[str, Any]] = [
        {"code": "basis"},
        {"code": "networth"},
        {"code": "app_differs"},
        {"code": "asof"},
        {"code": "currency"},
        {"code": "rounding"},
    ]
    if portfolios and not totals_provided:
        notes.append({"code": "totals_not_provided"})
    elif status_count["missing"]:
        notes.append({"code": "missing_totals", "n": status_count["missing"]})
    if withheld_count["liabilities"]:
        notes.append({"code": "liabilities", "n": withheld_count["liabilities"]})
    if withheld_count["unclassified"]:
        notes.append({"code": "unclassified", "n": withheld_count["unclassified"]})
    if legacy_shown:
        notes.append({"code": "legacy", "n": legacy_shown})
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


_NO_AMOUNT_LABEL = {
    "empty": "no_values",
    "unreadable": "unreadable",
    "missing": "not_available",
}


def _status_row(name: str, currencies: str, label: str) -> list[str]:
    """A table row that says why there is no amount (the label sits in the
    first amount column; the other five are empty)."""
    return [name, currencies, label, *([_EM_DASH] * 4)]


def _as_of_cell(as_of: str | None, *, unknown: bool, lang: str) -> str:
    """A date, "No items" (the host sent null) or "Not provided" (older host)."""
    if as_of:
        return as_of
    return STRINGS[lang]["asof_unknown" if unknown else "asof_none"]


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
                            assets=format_amount(e["total_assets"], e["currency"]),
                            liabilities=format_amount(
                                e["total_liabilities"], e["currency"]
                            ),
                            net=format_amount(e["net_worth"], e["currency"]),
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
        blocks.append(("p", s["portfolios_intro"].format(asof=asof)))
        rows: list[list[str]] = []
        for p in portfolios:
            name = _display_name(p, lang)
            st = p["totals_status"]
            for t in p["totals"]:
                cur = t["currency"]
                legacy = t["source"] == "legacy"
                rows.append(
                    [
                        name,
                        cur,
                        format_amount(t["assets"], cur),
                        _as_of_cell(t["assets_as_of"], unknown=legacy, lang=lang),
                        format_amount(t["liabilities"], cur),
                        _as_of_cell(t["liabilities_as_of"], unknown=False, lang=lang),
                        format_amount(t["net_worth"], cur),
                    ]
                )
            if p["withheld"]:
                label = s[_WITHHELD_LABEL[p["withheld"]["reason"]]]
                currencies = s["name_sep"].join(p["withheld"]["currencies"])
                rows.append(_status_row(name, currencies, label))
            elif st in _NO_AMOUNT_LABEL:
                rows.append(_status_row(name, _EM_DASH, s[_NO_AMOUNT_LABEL[st]]))
        blocks.append(
            (
                "table",
                [
                    s["th_portfolio"],
                    s["th_currency"],
                    s["th_assets"],
                    s["th_asof"],
                    s["th_liabilities"],
                    s["th_asof"],
                    s["th_net"],
                ],
                rows,
                ["l", "l", "r", "l", "r", "l", "r"],
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
