import json
from datetime import date
from decimal import Decimal

import pytest

from property_estimates.estimate import (
    LABEL,
    MIN_SAMPLE,
    MIN_SAMPLE_PROPOSE,
    MIN_SAMPLE_TAILS,
    Sale,
    _q,
    summarize,
)
from property_estimates.money import to_minor

AS_OF = date(2026, 10, 4)
KW = dict(
    currency="GBP",
    as_of=AS_OF,
    query={"district": "X"},
    source={"origin": "example.test", "fetched_at": "2026-10-04T12:00:00Z"},
    attribution="ATTR",
)


def sales(values, d=date(2026, 8, 1), area=None):
    return [Sale(d, to_minor(v), area) for v in values]


def run(values, **over):
    return summarize(sales(values), **{**KW, **over})


def no_floats(node):
    if isinstance(node, float):
        return False
    if isinstance(node, dict):
        return all(no_floats(v) for v in node.values())
    if isinstance(node, list):
        return all(no_floats(v) for v in node)
    return True


def test_minimum_sample_boundary():
    nine = run(range(100_000, 190_000, 10_000))
    assert len(range(100_000, 190_000, 10_000)) == 9
    assert nine["status"] == "insufficient_sample"
    assert "median" not in nine and "p25" not in nine
    assert "No figure is shown" in nine["message"]
    ten = run(range(100_000, 200_000, 10_000))
    assert ten["status"] == "ok" and ten["sample"]["n_used"] == MIN_SAMPLE


def test_exact_values_and_rounding():
    r = run(range(100_000, 200_000, 10_000))
    assert r["median"] == "145000.00"
    assert r["p25"] == "122500.00"
    assert r["p75"] == "167500.00"
    assert "p10" not in r  # fewer than MIN_SAMPLE_TAILS
    assert r["currency"] == "GBP"


def test_tails_need_twenty():
    r = run(range(100_000, 100_000 + 20 * 5_000, 5_000))
    assert MIN_SAMPLE_TAILS == 20 and "p10" in r and "p90" in r


def test_half_even_on_minor_units():
    assert _q([0, 1], 1, 2) == 0
    assert _q([1, 2], 1, 2) == 2
    assert _q([2, 3], 1, 2) == 2


def test_outlier_is_excluded_and_counted():
    vals = list(range(400_000, 400_000 + 19 * 5_000, 5_000)) + [9_000_000]
    r = run(vals)
    assert r["sample"]["n_raw"] == 20
    assert r["sample"]["n_used"] == 19
    assert r["sample"]["n_excluded_outliers"] == 1
    assert "max" not in r and "min" not in r


def test_outliers_cannot_shrink_below_minimum():
    # 10 raw sales, one wild value -> 9 left -> insufficient, never a figure
    vals = list(range(400_000, 400_000 + 9 * 5_000, 5_000)) + [50_000_000]
    r = run(vals)
    assert r["status"] == "insufficient_sample"
    assert r["sample"] == {"n_raw": 10, "n_used": 9}


def test_non_positive_prices_are_ignored():
    r = summarize(sales([0, -5] + list(range(100_000, 110_000, 1_000))), **KW)
    assert r["sample"]["n_raw"] == 10


def test_empty_and_identical_values():
    assert run([])["status"] == "insufficient_sample"
    same = run([300_000] * 12)
    assert same["status"] == "ok" and same["median"] == "300000.00"


@pytest.mark.parametrize(
    "n,level", [(10, "low"), (29, "low"), (30, "medium"), (99, "medium"), (100, "high")]
)
def test_confidence_levels(n, level):
    r = run([400_000 + i * 1_000 for i in range(n)])
    assert r["confidence"]["level"] == level
    assert "not adjusted for" in r["confidence"]["note"]


def test_stale_sales_lower_confidence():
    old = summarize(sales([400_000 + i * 1_000 for i in range(30)], d=date(2025, 6, 1)), **KW)
    assert old["confidence"]["level"] == "low"
    assert "more than 12 months old" in old["confidence"]["note"]


def test_per_sqm_requires_enough_areas():
    with_area = summarize(sales(range(100_000, 200_000, 10_000), area=Decimal("50")), **KW)
    assert with_area["per_sqm"]["n"] == 10
    assert with_area["per_sqm"]["median"] == "2900.00"  # 145000 / 50
    few = [
        Sale(date(2026, 8, 1), to_minor(100_000 + i * 10_000), Decimal("50") if i < 3 else None)
        for i in range(10)
    ]
    assert summarize(few, **KW)["per_sqm"] is None


def test_label_attribution_provenance_and_month_granularity():
    r = run(range(100_000, 200_000, 10_000))
    assert r["label"] == LABEL and "not a valuation" in LABEL
    assert r["attribution"] == "ATTR"
    assert r["source"]["fetched_at"] == "2026-10-04T12:00:00Z"
    assert r["query"] == {"district": "X"}
    assert r["sample"]["date_from"] == r["sample"]["date_to"] == "2026-08"
    assert "rounding" in r and "half-even" in r["rounding"]


def test_output_is_json_and_float_free():
    r = summarize(sales(range(100_000, 300_000, 5_000), area=Decimal("93.5")), **KW)
    assert no_floats(r)
    assert json.loads(json.dumps(r)) == r


def test_min_propose_is_stricter_than_display():
    assert MIN_SAMPLE_PROPOSE > MIN_SAMPLE
