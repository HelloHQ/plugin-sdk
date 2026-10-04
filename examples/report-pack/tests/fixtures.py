"""Host-context fixtures.

The shapes mirror what the real host hands a Tier-1 sidecar in ``args["context"]``
(``PluginSidecarBridge.snapshot`` -> ``PluginDataAccessObject`` in the hellohq
app, and the same shapes ``mock-host`` serves):

    "read:portfolio_names":   [{"id", "name"}, ...]
    "read:aggregated_values": {"portfolios": [{"id", "totals": [{"currency_id", "total"}]}]}

Currency ids are lowercase in the app; amounts are JSON numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Fixed report time so goldens are deterministic.
NOW = datetime(2026, 10, 4, 12, 0, 30, tzinfo=UTC)

NAMES = "read:portfolio_names"
TOTALS = "read:aggregated_values"


def names(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"id": pid, "name": name} for pid, name in pairs]


def totals(**by_portfolio: dict[str, Any]) -> dict[str, Any]:
    """``totals(ptf_a={"usd": 1.5})`` -> the aggregated-values read."""
    return {
        "portfolios": [
            {
                "id": pid,
                "totals": [{"currency_id": c, "total": v} for c, v in cur.items()],
            }
            for pid, cur in by_portfolio.items()
        ]
    }


def ctx(names_read: Any = None, totals_read: Any = None) -> dict[str, Any]:
    """A context; ``None`` omits that read (as a denied permission would)."""
    out: dict[str, Any] = {}
    if names_read is not None:
        out[NAMES] = names_read
    if totals_read is not None:
        out[TOTALS] = totals_read
    return out


# ── Scenarios used by the golden tests ──────────────────────────────────────

FAMILY = ctx(
    names(
        ("ptf_home", "Family Home"),
        ("ptf_invest", "Investments"),
        ("ptf_biz", "Chen & Sons Trading"),
    ),
    totals(
        ptf_home={"sgd": 1250000.0, "cny": 480000.5},
        ptf_invest={"usd": 215340.126, "sgd": 98000.0, "jpy": 3500000},
        ptf_biz={"usd": 48000.0},
    ),
)

EMPTY = ctx([], {"portfolios": []})

NO_TOTALS = ctx(names(("ptf_a", "Personal"), ("ptf_b", "Business")))

PARTIAL = ctx(
    names(("ptf_a", "Personal"), ("ptf_b", "Business"), ("ptf_c", "Archive")),
    totals(ptf_a={"usd": 1000.0}, ptf_c={}),
)

NEGATIVE = ctx(
    names(("ptf_a", "Mortgage Property"), ("ptf_b", "Cash")),
    totals(ptf_a={"sgd": -350000.75}, ptf_b={"sgd": 20000.0, "usd": -0.004}),
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
        p1={"usd": 1.0},
        p2={"usd": 2.0},
        p3={"cny": 3.0},
        p4={"eur": 4.0},
        p5={"gbp": 5.0},
    ),
)

SCENARIOS = {
    "family": FAMILY,
    "empty": EMPTY,
    "no_totals": NO_TOTALS,
    "partial": PARTIAL,
    "negative": NEGATIVE,
    "special": SPECIAL,
}
