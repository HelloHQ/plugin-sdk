"""Build (never submit on its own) a propose-only valuation from an estimate.

The plugin proposes a value against ITS OWN source key. It never sees item ids and never
writes anything: the host queues the proposal for the person to review. ``build_valuation_proposal``
returns the PREVIEW (the wire fields plus ``confidence``, ``rounding``, ``disclaimer`` and
``attribution`` for display); ``to_wire`` is what goes to the host, which refuses unknown
fields. The wire shape is ``hellohq.proposal-batch@1`` (plugin-protocol ``host-calls.schema.json``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import ProposalRefused, ValidationError
from .estimate import LABEL, MIN_SAMPLE_PROPOSE
from .money import minor_to_str, parse_decimal, round_for_proposal, to_minor

METHOD = "comparable_sales_median"
#: The fields of a ``valuation`` proposal the host accepts; the rest of the preview is display-only.
WIRE_FIELDS = ("kind", "source_key", "value", "as_of", "method", "source")
#: ``source.origin`` must be a host name; a person-provided file names its publisher.
PERSON_PROVIDED_ORIGIN = {"ie_ppr": "propertypriceregister.ie"}
_KEY_RE = re.compile(r"^[A-Za-z0-9:._/-]{1,256}$")


def default_source_key(region: str, query: Mapping[str, Any]) -> str:
    parts = [region] + [
        f"{k}:{re.sub(r'[^A-Za-z0-9._-]+', '-', str(v)).strip('-')}"
        for k, v in sorted(query.items())
        if k not in ("months", "region") and v not in ("", None) and not isinstance(v, (list, dict))
    ]
    return ":".join(parts)[:256]


def to_wire(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a preview proposal that goes to the host (nothing the host would refuse)."""
    return {key: proposal[key] for key in WIRE_FIELDS if key in proposal}


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
        "as_of": f"{src['fetched_at'][:10]}T00:00:00Z",  # RFC 3339 UTC: the day the sales were read
        "method": METHOD,
        "source": {
            "origin": PERSON_PROVIDED_ORIGIN.get(region, src["origin"]),
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
