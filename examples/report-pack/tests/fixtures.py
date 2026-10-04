"""Host-context fixtures.

The shapes mirror what the real host hands a Tier-1 sidecar in ``args["context"]``
(``PluginSidecarBridge.snapshot`` -> ``PluginDataAccessObject`` in the hellohq
app):

    "read:portfolio_names":   [{"id", "name"}, ...]
    "read:asset_count":       {"portfolios": [{"id", "asset_items", "debt_items",
                               "total_items"}]}
    "read:currency_rates":    [{"id", "name", "symbol", "rate"}]
    "read:aggregated_values": {"portfolios": [{"id", "totals": [{"currency_id",
                               "total"}]}]}

``currency_id`` is the workspace currency *row id*, not a code: the app's
built-in currencies use fixed UUIDs (``uuid_for_currency.dart``), import-created
ones ``document-import-currency-<CODE>``, user-created ones any id with a code
in their ``name``. Amounts are JSON numbers (the host converts its exact
Decimal sum to a double at the boundary). ``rate`` is micro-units per USD and
is never used by the plugin.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


def totals(**by_portfolio: dict[str, Any]) -> dict[str, Any]:
    """``totals(ptf_a={USD: 1.5})`` -> the aggregated-values read."""
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


def debt_free(*pids: str) -> dict[str, Any]:
    """Asset counts saying every listed portfolio has no liability items."""
    return counts(**{pid: (1, 0) for pid in pids})


def ctx(
    names_read: Any = None,
    totals_read: Any = None,
    counts_read: Any = None,
    currencies_read: Any = CURRENCY_LIST,
) -> dict[str, Any]:
    """A context; ``None`` omits that read (as a denied permission would).

    By default every portfolio named in ``totals_read`` is debt-free, so tests
    about amounts do not have to repeat the counts. Pass ``counts_read={}`` to
    send an empty asset-count read, or ``counts_read=False`` to omit it.
    """
    out: dict[str, Any] = {}
    if names_read is not None:
        out[NAMES] = names_read
    if totals_read is not None:
        out[TOTALS] = totals_read
    if counts_read is None and isinstance(totals_read, dict):
        pids = [
            str(e["id"])
            for e in totals_read.get("portfolios", [])
            if isinstance(e, dict) and "id" in e
        ]
        counts_read = debt_free(*pids)
    if counts_read is not None and counts_read is not False:
        out[COUNTS] = counts_read
    if currencies_read is not None:
        out[CURRENCIES] = currencies_read
    return out


# ── Scenarios used by the golden tests ──────────────────────────────────────

# A family with a mortgaged home (withheld), debt-free investments, a business
# whose composition the host did not report, and one empty portfolio.
FAMILY = ctx(
    names(
        ("ptf_home", "Family Home"),
        ("ptf_invest", "Investments"),
        ("ptf_biz", "Chen & Sons Trading"),
        ("ptf_cash", "Cash Savings"),
        ("ptf_new", "New Portfolio"),
    ),
    totals(
        ptf_home={SGD: 1250000.0, CNY: 480000.5},
        ptf_invest={USD: 215340.126, SGD: 98000.0, JPY: 3500000},
        ptf_biz={USD: 48000.0},
        ptf_cash={SGD: 42000.0, USD: 1500.25},
        ptf_new={},
    ),
    counts(ptf_home=(2, 1), ptf_invest=(5, 0), ptf_cash=(2, 0), ptf_new=(0, 0)),
)

# Every portfolio debt-free: the report shows every amount.
ASSETS_ONLY = ctx(
    names(("ptf_a", "Personal"), ("ptf_b", "Business")),
    totals(ptf_a={USD: 1000.0, SGD: 250.5}, ptf_b={USD: 2000.0, EUR: 99.99}),
)

# Every portfolio holds liabilities: no amount at all.
ALL_LIABILITIES = ctx(
    names(("ptf_a", "Home"), ("ptf_b", "Car")),
    totals(ptf_a={SGD: 900000.0}, ptf_b={SGD: 30000.0}),
    counts(ptf_a=(1, 1), ptf_b=(1, 1)),
)

EMPTY = ctx([], {"portfolios": []}, counts_read={"portfolios": []})

NO_TOTALS = ctx(names(("ptf_a", "Personal"), ("ptf_b", "Business")))

PARTIAL = ctx(
    names(("ptf_a", "Personal"), ("ptf_b", "Business"), ("ptf_c", "Archive")),
    totals(ptf_a={USD: 1000.0}, ptf_c={}),
)

NEGATIVE = ctx(
    names(("ptf_a", "Overdrawn Account"), ("ptf_b", "Cash")),
    totals(ptf_a={SGD: -350000.75}, ptf_b={SGD: 20000.0, USD: -0.004}),
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

SCENARIOS = {
    "family": FAMILY,
    "assets_only": ASSETS_ONLY,
    "all_liabilities": ALL_LIABILITIES,
    "empty": EMPTY,
    "no_totals": NO_TOTALS,
    "partial": PARTIAL,
    "negative": NEGATIVE,
    "special": SPECIAL,
}
