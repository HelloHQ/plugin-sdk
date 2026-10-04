"""Comparable-sales summary: the estimator, its minimum-sample rule and its labels.

INFORMATION ONLY. The output is a median and percentiles of recent comparable sales for an
area and property type the person chose. It is not a valuation of any specific property.

Minimum-sample rule (documented in the README):
  * fewer than MIN_SAMPLE usable sales (after outlier removal): no figure is produced at all;
  * p10 / p90 are shown only with MIN_SAMPLE_TAILS or more sales;
  * a value may be *proposed* only with MIN_SAMPLE_PROPOSE or more sales.
The smallest and largest individual sale are never output (aggregate-only).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .money import minor_to_str
from .stats import quantile, tukey_bounds

LABEL = (
    "Information only. This is a summary of recent comparable sales, not a valuation, "
    "appraisal or advice, and it says nothing certain about any particular property."
)
MIN_SAMPLE = 10
MIN_SAMPLE_TAILS = 20
MIN_SAMPLE_PROPOSE = 30
STALE_AFTER_DAYS = 365
ROUNDING_NOTE = (
    "Money is exact decimal. Percentiles use linear interpolation computed with exact "
    "fractions, then rounded once, half-even, to 2 decimal places."
)


@dataclass(frozen=True)
class Sale:
    """The only per-sale facts the plugin retains. No address, no identifier."""

    sold_on: date
    price_minor: int
    area_sqm: Decimal | None = None


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _confidence(n_used: int, newest: date, as_of: date) -> tuple[str, str]:
    if n_used < MIN_SAMPLE:
        return "none", "Too few comparable sales for any figure."
    level = "low" if n_used < 30 else "medium" if n_used < 100 else "high"
    notes = [f"Based on {n_used} comparable sales."]
    if (as_of - newest).days > STALE_AFTER_DAYS:
        order = ["low", "medium", "high"]
        level = order[max(order.index(level) - 1, 0)]
        notes.append("The newest sale is more than 12 months old; confidence lowered.")
    notes.append(
        "Sales are matched only on the area and property type you chose; size, condition, "
        "floor, lease length and exact location are not adjusted for."
    )
    return level, " ".join(notes)


def summarize(
    sales: Iterable[Sale],
    *,
    currency: str,
    as_of: date,
    query: Mapping[str, Any],
    source: Mapping[str, Any],
    attribution: str,
    skipped: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Summarise comparable sales. Returns a JSON-ready dict (strings and ints only)."""
    valid = [s for s in sales if s.price_minor > 0]
    n_raw = len(valid)
    base: dict[str, Any] = {
        "label": LABEL,
        "currency": currency,
        "query": dict(query),
        "source": dict(source),
        "attribution": attribution,
        "rows_skipped": dict(skipped or {}),
        "min_sample": MIN_SAMPLE,
        "rounding": ROUNDING_NOTE,
    }
    if n_raw < MIN_SAMPLE:
        return {**base, **_insufficient(n_raw, n_raw)}

    prices = sorted(s.price_minor for s in valid)
    low, high = tukey_bounds(prices)
    kept = [s for s in valid if low <= s.price_minor <= high]
    n_used = len(kept)
    if n_used < MIN_SAMPLE:
        return {**base, **_insufficient(n_raw, n_used)}

    kept_prices = sorted(s.price_minor for s in kept)
    newest = max(s.sold_on for s in kept)
    oldest = min(s.sold_on for s in kept)
    level, note = _confidence(n_used, newest, as_of)
    figures: dict[str, Any] = {
        "median": minor_to_str(_q(kept_prices, 1, 2)),
        "p25": minor_to_str(_q(kept_prices, 1, 4)),
        "p75": minor_to_str(_q(kept_prices, 3, 4)),
    }
    if n_used >= MIN_SAMPLE_TAILS:
        figures["p10"] = minor_to_str(_q(kept_prices, 1, 10))
        figures["p90"] = minor_to_str(_q(kept_prices, 9, 10))

    per_sqm_vals = sorted(
        Fraction(s.price_minor) / Fraction(s.area_sqm)
        for s in kept
        if s.area_sqm is not None and s.area_sqm > 0
    )
    per_sqm: dict[str, Any] | None = None
    if len(per_sqm_vals) >= MIN_SAMPLE:
        per_sqm = {
            "n": len(per_sqm_vals),
            "median": minor_to_str(_q(per_sqm_vals, 1, 2)),
            "p25": minor_to_str(_q(per_sqm_vals, 1, 4)),
            "p75": minor_to_str(_q(per_sqm_vals, 3, 4)),
        }

    return {
        **base,
        "status": "ok",
        "sample": {
            "n_raw": n_raw,
            "n_used": n_used,
            "n_excluded_outliers": n_raw - n_used,
            "outlier_rule": "Tukey fences, 1.5 x IQR, on price",
            "date_from": _month(oldest),
            "date_to": _month(newest),
        },
        **figures,
        "per_sqm": per_sqm,
        "confidence": {"level": level, "note": note},
    }


def _q(sorted_minor: Sequence[int | Fraction], num: int, den: int) -> int:
    """Quantile of minor-unit values, rounded once (half-even) to whole minor units."""
    return round(quantile(sorted_minor, Fraction(num, den)))


def _insufficient(n_raw: int, n_used: int) -> dict[str, Any]:
    return {
        "status": "insufficient_sample",
        "sample": {"n_raw": n_raw, "n_used": n_used},
        "message": (
            f"Only {n_used} usable comparable sales were found; at least {MIN_SAMPLE} are "
            "needed. No figure is shown rather than guessing from too few sales. Try a wider "
            "area or a longer period."
        ),
        "confidence": {"level": "none", "note": "Too few comparable sales for any figure."},
    }
