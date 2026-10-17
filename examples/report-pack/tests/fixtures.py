"""Host-context fixtures.

The shapes mirror what the real host hands a Tier-1 sidecar in ``args["context"]``
(``PluginSidecarBridge.snapshot`` -> ``PluginDataAccessObject`` in the hellohq
app; ``readAggregatedValues`` and test/unit/database/
plugin_pdao_aggregated_totals_test.dart there):

    "read:portfolio_names":   [{"id", "name"}, ...]
    "read:asset_count":       {"portfolios": [{"id", "asset_items", "debt_items",
                               "total_items"}]}
    "read:currency_rates":    [{"id", "name", "symbol", "rate"}]
    "read:aggregated_values": {"portfolios": [{"id", "totals": [{
                               "currency_id", "assets", "debts", "assets_as_of",
                               "debts_as_of", "total"}]}]}

``currency_id`` is the workspace currency *row id*, not a code: the app's
built-in currencies use fixed UUIDs (``uuid_for_currency.dart``), import-created
ones ``document-import-currency-<CODE>``, user-created ones any id with a code
in their ``name``. ``assets`` and ``debts`` are exact decimal strings (debts are
positive); ``assets_as_of`` / ``debts_as_of`` are ISO-8601 UTC timestamps or
``null`` when no item of that kind counts; ``total`` is the deprecated, inexact
double ``assets + debts``. An OLDER host sends only ``currency_id`` and
``total``: ``legacy_totals`` builds that shape. ``rate`` is micro-units per USD
and is never used by the plugin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

#: Fixed report time so goldens are deterministic.
NOW = datetime(2026, 10, 4, 12, 0, 30, tzinfo=UTC)

NAMES = "read:portfolio_names"
COUNTS = "read:asset_count"
CURRENCIES = "read:currency_rates"
TOTALS = "read:aggregated_values"

# Built-in currency row ids (hellohq lib/app/utils/constant/uuid_for_currency.dart).
USD = "90dec588-8241-4d40-870a-3c8259d99c91"
CNY = "42feb0cf-228e-4d81-beb2-9ff881a0b28d"
SGD = "c4ca5196-f391-4f58-9d13-8f2e61bef5cc"
GBP = "fc130635-6be6-4c88-9fc2-b7541c153f97"
JPY = "65edf18a-d6d1-41fe-b429-b0dbeeedb4a8"
IDR = "73bfdb96-39fc-4e0a-975d-e528b71f0647"
# A user-created currency row (code only in its name) and an import-created one.
EUR = "3b0d5f7e-0c3e-4a8e-9c55-6f2d1d6b2f10"
KWD = "document-import-currency-KWD"

#: The workspace currency list as ``read:currency_rates`` returns it.
CURRENCY_LIST = [
    {"id": USD, "name": "USD", "symbol": "$", "rate": 1000000},
    {"id": CNY, "name": "CNY", "symbol": "CN¥", "rate": 7184800},
    {"id": SGD, "name": "SGD", "symbol": "SGD", "rate": 1282600},
    {"id": GBP, "name": "GBP", "symbol": "£", "rate": 790000},
    {"id": JPY, "name": "JPY", "symbol": "¥", "rate": 157000000},
    {"id": EUR, "name": "Euro zone", "symbol": "EUR", "rate": 920000},
    {"id": KWD, "name": "KWD", "symbol": "KWD", "rate": 1000000},
]


def names(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"id": pid, "name": name} for pid, name in pairs]


#: Default as-of timestamps in the host's format (``DateTime.toIso8601String``).
ASSETS_AS_OF = "2026-09-30T00:00:00.000Z"
DEBTS_AS_OF = "2026-09-15T00:00:00.000Z"
_DEFAULT = object()


def amount_text(value: Any) -> str:
    """A number or string as the host's exact decimal string (never via float)."""
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        return format(Decimal(repr(value)), "f")
    return str(value)


def split_row(
    currency_id: str,
    assets: Any,
    debts: Any = 0,
    assets_as_of: Any = _DEFAULT,
    debts_as_of: Any = _DEFAULT,
) -> dict[str, Any]:
    """A ``totals`` row as the current host sends it.

    Unless given, an as-of date is present exactly when that side is non-zero
    (the host sends null when no item of that kind counts).
    """
    a, d = amount_text(assets), amount_text(debts)
    if assets_as_of is _DEFAULT:
        assets_as_of = ASSETS_AS_OF if Decimal(a) != 0 else None
    if debts_as_of is _DEFAULT:
        debts_as_of = DEBTS_AS_OF if Decimal(d) != 0 else None
    return {
        "currency_id": currency_id,
        "total": float(Decimal(a) + Decimal(d)),  # deprecated; adds debts in
        "assets": a,
        "debts": d,
        "assets_as_of": assets_as_of,
        "debts_as_of": debts_as_of,
    }


def totals(**by_portfolio: dict[str, Any]) -> dict[str, Any]:
    """``totals(ptf_a={USD: 1.5, SGD: (10, 4)})`` -> the aggregated-values read.

    A value is the assets (debts 0), or an ``(assets, debts)`` pair, or a ready
    row dict (``split_row`` output) for full control.
    """

    def row(currency_id: str, spec: Any) -> dict[str, Any]:
        if isinstance(spec, dict):
            return spec
        if isinstance(spec, tuple):
            return split_row(currency_id, *spec)
        return split_row(currency_id, spec)

    return {
        "portfolios": [
            {"id": pid, "totals": [row(c, v) for c, v in cur.items()]}
            for pid, cur in by_portfolio.items()
        ]
    }


def legacy_totals(**by_portfolio: dict[str, Any]) -> dict[str, Any]:
    """``legacy_totals(ptf_a={USD: 1.5})``: an older host, ``total`` (a double)
    only, which adds debts to assets."""
    return {
        "portfolios": [
            {
                "id": pid,
                "totals": [{"currency_id": c, "total": v} for c, v in cur.items()],
            }
            for pid, cur in by_portfolio.items()
        ]
    }


def counts(**by_portfolio: tuple[int, int]) -> dict[str, Any]:
    """``counts(ptf_a=(3, 1))`` -> the asset-count read (assets, liabilities)."""
    return {
        "portfolios": [
            {"id": pid, "asset_items": a, "debt_items": d, "total_items": a + d}
            for pid, (a, d) in by_portfolio.items()
        ]
    }


def _derived_counts(totals_read: Any) -> dict[str, Any]:
    """Item counts consistent with the totals, as a real host would send them.

    A row with an ``assets_as_of`` has an asset item, a row with a
    ``debts_as_of`` a liability item; an old-style row is taken as debt-free.
    """
    per: dict[str, tuple[int, int]] = {}
    for e in totals_read.get("portfolios", []):
        if not (isinstance(e, dict) and "id" in e):
            continue
        a = d = 0
        for r in e.get("totals", []):
            if not isinstance(r, dict):
                continue
            if "assets" in r or "debts" in r:
                a += r.get("assets_as_of") is not None
                d += r.get("debts_as_of") is not None
            else:
                a += 1
        per[str(e["id"])] = (a, d)
    return counts(**per)


def ctx(
    names_read: Any = None,
    totals_read: Any = None,
    counts_read: Any = None,
    currencies_read: Any = CURRENCY_LIST,
) -> dict[str, Any]:
    """A context; ``None`` omits that read (as a denied permission would).

    By default the asset-count read is derived from the totals (see
    ``_derived_counts``), so tests need not repeat it. Pass ``counts_read={}``
    to send an empty asset-count read, or ``counts_read=False`` to omit it.
    """
    out: dict[str, Any] = {}
    if names_read is not None:
        out[NAMES] = names_read
    if totals_read is not None:
        out[TOTALS] = totals_read
    if counts_read is None and isinstance(totals_read, dict):
        counts_read = _derived_counts(totals_read)
    if counts_read is not None and counts_read is not False:
        out[COUNTS] = counts_read
    if currencies_read is not None:
        out[CURRENCIES] = currencies_read
    return out


# ── Scenarios used by the golden tests ──────────────────────────────────────

# A family with a mortgaged home, debt-free investments, a business with a loan,
# a cash portfolio in two currencies and one empty portfolio. Amounts are the
# host's exact strings (a Dart ``Decimal`` prints no trailing zeros).
FAMILY = ctx(
    names(
        ("ptf_home", "Family Home"),
        ("ptf_invest", "Investments"),
        ("ptf_biz", "Chen & Sons Trading"),
        ("ptf_cash", "Cash Savings"),
        ("ptf_new", "New Portfolio"),
    ),
    totals(
        ptf_home={SGD: ("1250000", "780000.5"), CNY: "480000.5"},
        ptf_invest={USD: "215340.126", SGD: "98000", JPY: "3500000"},
        ptf_biz={USD: ("48000", "12500.75")},
        ptf_cash={SGD: "42000", USD: "1500.25"},
        ptf_new={},
    ),
)

# Every portfolio debt-free: no liabilities anywhere.
ASSETS_ONLY = ctx(
    names(("ptf_a", "Personal"), ("ptf_b", "Business")),
    totals(ptf_a={USD: 1000.0, SGD: 250.5}, ptf_b={USD: 2000.0, EUR: 99.99}),
)

# Every portfolio holds liabilities: each still shows its amounts.
ALL_LIABILITIES = ctx(
    names(("ptf_a", "Home"), ("ptf_b", "Car")),
    totals(ptf_a={SGD: ("1500000", "900000")}, ptf_b={SGD: ("45000", "30000.25")}),
)

EMPTY = ctx([], {"portfolios": []}, counts_read={"portfolios": []})

NO_TOTALS = ctx(names(("ptf_a", "Personal"), ("ptf_b", "Business")))

PARTIAL = ctx(
    names(("ptf_a", "Personal"), ("ptf_b", "Business"), ("ptf_c", "Archive")),
    totals(ptf_a={USD: 1000.0}, ptf_c={}),
)

# A negative asset total (overdrawn), liabilities above assets (negative net
# worth) and a value that rounds to zero.
NEGATIVE = ctx(
    names(("ptf_a", "Overdrawn Account"), ("ptf_b", "Cash"), ("ptf_c", "Underwater")),
    totals(
        ptf_a={SGD: "-350000.75"},
        ptf_b={SGD: "20000", USD: "-0.004"},
        ptf_c={SGD: ("400000", "650000.5")},
    ),
)

SPECIAL = ctx(
    names(
        ("p1", "Savings | 2026 *draft* [x](http://evil) <b>bold</b>"),
        ("p2", "A" * 120),
        ("p3", "家庭基金 \u202eevil\u202c\nline2"),
        ("p4", "<script>alert(1)</script> & co"),
        ("p5", ""),
    ),
    totals(
        p1={USD: 1.0},
        p2={USD: 2.0},
        p3={CNY: 3.0},
        p4={EUR: 4.0},
        p5={GBP: 5.0},
    ),
)

# An OLDER host: only the combined ``total`` (assets + debts, as a double). The
# 1.0.0 behaviour for such rows: shown only for a portfolio the item counts say
# has no liabilities, withheld otherwise or when that cannot be told.
LEGACY = ctx(
    names(
        ("ptf_home", "Family Home"),
        ("ptf_invest", "Investments"),
        ("ptf_biz", "Chen & Sons Trading"),
        ("ptf_cash", "Cash Savings"),
        ("ptf_new", "New Portfolio"),
    ),
    legacy_totals(
        ptf_home={SGD: 1250000.0, CNY: 480000.5},
        ptf_invest={USD: 215340.126, SGD: 98000.0, JPY: 3500000},
        ptf_biz={USD: 48000.0},
        ptf_cash={SGD: 42000.0, USD: 1500.25},
        ptf_new={},
    ),
    counts(ptf_home=(2, 1), ptf_invest=(5, 0), ptf_cash=(2, 0), ptf_new=(0, 0)),
)

# An older host, every portfolio with liabilities: nothing can be shown.
LEGACY_WITHHELD = ctx(
    names(("ptf_a", "Home"), ("ptf_b", "Car")),
    legacy_totals(ptf_a={SGD: 900000.0}, ptf_b={SGD: 30000.0}),
    counts(ptf_a=(1, 1), ptf_b=(1, 1)),
)

SCENARIOS = {
    "family": FAMILY,
    "assets_only": ASSETS_ONLY,
    "all_liabilities": ALL_LIABILITIES,
    "empty": EMPTY,
    "no_totals": NO_TOTALS,
    "partial": PARTIAL,
    "negative": NEGATIVE,
    "special": SPECIAL,
    "legacy": LEGACY,
    "legacy_withheld": LEGACY_WITHHELD,
}
