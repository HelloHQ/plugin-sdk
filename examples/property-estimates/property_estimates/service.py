"""Orchestration: validate input, collect sales through the Host, summarise, optionally
build a proposal preview. This is what the sidecar adapter calls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import PendingHostSupport, ProposeRefused, ValidationError
from .estimate import summarize
from .fr_dvf import FR_ATTRIBUTION, FR_CURRENCY, fr_collect, fr_validate_params
from .hostapi import Host
from .ie_ppr import IE_ATTRIBUTION, IE_CURRENCY, ie_validate_params, parse_ppr_csv
from .proposal import build_valuation_proposal, to_wire
from .sg_hdb import SG_ATTRIBUTION, SG_CURRENCY, sg_collect, sg_validate_params
from .transport import FR_ORIGIN, SG_ORIGIN, UK_ORIGIN, Fetcher, window_start
from .uk_ppd import UK_CURRENCY, parse_ppd_csv, uk_attribution, uk_collect, uk_validate_params

REGIONS = ("sg_hdb", "uk_ppd", "fr_dvf", "ie_ppr")
_ORIGIN = {
    "sg_hdb": SG_ORIGIN,
    "uk_ppd": UK_ORIGIN,
    "fr_dvf": FR_ORIGIN,
    "ie_ppr": "person-provided CSV (PSRA Residential Property Price Register)",
}


def estimate_property(host: Host, request: Mapping[str, Any]) -> dict[str, Any]:
    """Run one estimate. ``request`` is the person's input; see the README for each region."""
    region = request.get("region")
    if region not in REGIONS:
        raise ValidationError(f"region: one of {list(REGIONS)}")
    now = host.now()
    as_of = now.date()
    fetched_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fetcher = Fetcher(host)

    if region == "sg_hdb":
        params = sg_validate_params(request)
        sales, skipped, refs = sg_collect(fetcher, params, as_of=as_of)
        currency, attribution = SG_CURRENCY, SG_ATTRIBUTION
    elif region == "uk_ppd":
        params = uk_validate_params(request)
        csv_text = request.get("csv_text")
        if isinstance(csv_text, str) and csv_text.strip():
            since = window_start(as_of, params["months"])
            sales, skipped = parse_ppd_csv(
                csv_text,
                district=params["district"],
                town=params["town"] or None,
                property_type=params["property_type"] or None,
                since=since,
            )
            refs = ["person-provided Price Paid CSV"]
        else:
            sales, skipped, refs = uk_collect(fetcher, params, as_of=as_of)
        currency, attribution = UK_CURRENCY, uk_attribution(as_of.year)
    elif region == "fr_dvf":
        params = fr_validate_params(request, as_of=as_of)
        sales, skipped, refs = fr_collect(fetcher, params, as_of=as_of)
        currency, attribution = FR_CURRENCY, FR_ATTRIBUTION
    else:
        params = ie_validate_params(request)
        sales, skipped = parse_ppr_csv(
            request["csv_text"],
            county=params["county"],
            since=window_start(as_of, params["months"]),
            dwelling=params["dwelling"],
        )
        refs = ["person-provided PPR CSV"]
        currency, attribution = IE_CURRENCY, IE_ATTRIBUTION

    result = summarize(
        sales,
        currency=currency,
        as_of=as_of,
        query={"region": region, **params},
        source={
            "origin": _ORIGIN[region],
            "fetched_at": fetched_at,
            "references": refs[:12],
            "requests_made": fetcher.requests_made,
        },
        attribution=attribution,
        skipped=skipped,
    )
    result["region"] = region
    if request.get("include_proposal_preview"):
        try:
            result["proposal_preview"] = build_valuation_proposal(
                result, region=region, source_key=request.get("source_key")
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as data, never raised
            result["proposal_preview"] = None
            result["proposal_preview_refused"] = getattr(exc, "message", str(exc))
    return result


def submit_proposal(host: Host, estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Hand the proposal to the host and report what came back. Returns, always as plain data:

    * ``{"status": "submitted", "receipts": [{"index", "outcome", "reason"?}]}`` - one receipt per
      proposal (``queued``, ``duplicate``, ``superseded_older``, ``unchanged``, ``suppressed``,
      ``invalid`` + a host reason code);
    * ``{"status": "pending_host_support", ...}`` - the host has no ``propose`` (it answered
      ``unknown_method``, or the installed SDK predates it): nothing was sent. The graceful
      fallback; it needs a host with propose-only writes (hellohq with plugins enabled);
    * ``{"status": "permission_denied" | "failed", "code", "retryable", ...}`` - the host
      refused the call (no usable ``propose:valuations`` grant here, rate limit, quota, ...).

    Raises ``ProposalRefused`` when the estimate does not meet the minimum-sample rule (a rule
    of this plugin, checked before anything is sent).
    """
    proposal = build_valuation_proposal(estimate, region=str(estimate.get("region", "")))
    try:
        receipts = host.propose([to_wire(proposal)])
    except PendingHostSupport as exc:
        return {
            "status": "pending_host_support",
            "code": exc.code,
            "message": exc.message,
            "receipts": [],
        }
    except ProposeRefused as exc:
        out: dict[str, Any] = {
            "status": "permission_denied" if exc.code == "permission_denied" else "failed",
            "code": exc.code,
            "retryable": exc.retryable,
            "message": exc.message,
            "receipts": [],
        }
        if exc.reason:
            out["reason"] = exc.reason
        return out
    rows = []
    for receipt in receipts:
        row: dict[str, Any] = {"index": receipt.index, "outcome": receipt.outcome}
        if receipt.reason:
            row["reason"] = receipt.reason
        rows.append(row)
    return {"status": "submitted", "receipts": rows}
