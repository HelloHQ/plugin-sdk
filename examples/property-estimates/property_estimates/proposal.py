"""Build (never submit on its own) a propose-only valuation from an estimate.

The plugin proposes a value against ITS OWN source key. It never sees item ids and never
writes anything: the host (when its propose API exists) queues the proposal for the person
to review. Field names here are provisional and will be aligned to the host contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import ProposalRefused, ValidationError
from .estimate import LABEL, MIN_SAMPLE_PROPOSE
from .money import minor_to_str, parse_decimal, round_for_proposal, to_minor

METHOD = "comparable_sales_median"
_KEY_RE = re.compile(r"^[A-Za-z0-9:._/-]{1,256}$")


def default_source_key(region: str, query: Mapping[str, Any]) -> str:
    parts = [region] + [
        f"{k}:{re.sub(r'[^A-Za-z0-9._-]+', '-', str(v)).strip('-')}"
        for k, v in sorted(query.items())
        if k not in ("months", "region") and v not in ("", None) and not isinstance(v, (list, dict))
    ]
    return ":".join(parts)[:256]


def build_valuation_proposal(
    estimate: Mapping[str, Any], *, region: str, source_key: str | None = None
) -> dict[str, Any]:
    """Return one proposal dict, or raise ProposalRefused if the minimum-sample rule fails."""
    if estimate.get("status") != "ok":
        raise ProposalRefused("estimate has no figure; nothing to propose")
    sample = estimate["sample"]
    if sample["n_used"] < MIN_SAMPLE_PROPOSE:
        raise ProposalRefused(
            f"a value may be proposed only from {MIN_SAMPLE_PROPOSE} or more comparable sales "
            f"(this estimate has {sample['n_used']})"
        )
    key = source_key or default_source_key(region, estimate["query"])
    if not _KEY_RE.match(key):
        raise ValidationError("source_key: 1-256 chars of A-Z a-z 0-9 : . _ / -")
    median_minor = to_minor(parse_decimal(estimate["median"], field="median"))
    proposed = round_for_proposal(median_minor)
    src = estimate["source"]
    return {
        "kind": "valuation",
        "source_key": key,
        "value": {"amount": minor_to_str(proposed), "currency": estimate["currency"]},
        "as_of": src["fetched_at"][:10],
        "method": METHOD,
        "source": {
            "origin": src["origin"],
            "reference": (
                f"median of {sample['n_used']} comparable sales "
                f"{sample['date_from']}..{sample['date_to']}; query {estimate['query']}"
            )[:200],
            "fetched_at": src["fetched_at"],
        },
        "confidence": estimate["confidence"],
        "rounding": (
            "Median rounded to the nearest 1,000 (100,000 and above) or 100 (below), "
            "half-even, so the proposal does not claim false precision."
        ),
        "disclaimer": LABEL,
        "attribution": estimate["attribution"],
    }
