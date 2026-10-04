"""Proposal construction, pre-flight validation and provenance.

The shape follows the host's propose-only write DESIGN (internal, unpublished)
(``hellohq.proposal-batch@1``). That API is designed but NOT built; nothing in
this module is a protocol definition, and the shape may change when the host
ships it. Every proposal carries provenance: the source's origin, the exact
request path used, the fetch time, and the identifier (``source_key``).

The plugin never learns item ids and never writes. It proposes against its
own source key; the person confirms a binding in the host UI.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from wallet_tracker.errors import ValidationError
from wallet_tracker.host import iso_utc
from wallet_tracker.money import CURRENCY_EXPONENTS, assert_no_floats

SCHEMA = "hellohq.proposal-batch@1"
ASSET_KIND = "crypto_ticker"  # the add-asset tile id
MAX_PROPOSALS_PER_CALL = 200
MAX_NEW_HOLDINGS_PER_BATCH = 50
_SOURCE_KEY = re.compile(r"^[A-Za-z0-9:._/-]{1,256}$")
_AMOUNT = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_URLISH = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class Provenance:
    """Where a proposed value came from. Serialised into ``source``."""

    origin: str  # host name actually contacted, e.g. "mempool.space"
    reference: str  # request path (+ short qualifier); plain text, <= 200 chars
    fetched_at: str  # RFC 3339 UTC
    price_origin: str | None = None
    price_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"origin": self.origin, "reference": self.reference, "fetched_at": self.fetched_at}
        if self.price_origin:
            out["price_origin"] = self.price_origin
        return out


def short_address(address: str) -> str:
    return address if len(address) <= 12 else f"{address[:6]}...{address[-4:]}"


def btc_source_key(address: str) -> str:
    return f"btc:address:{address}"


def sol_source_key(address: str, mint: str | None = None) -> str:
    return f"sol:address:{address}" if mint is None else f"sol:address:{address}:mint:{mint}"


def holding(
    *,
    source_key: str,
    display_name: str,
    symbol: str | None,
    chain: str,
    quantity: str,
    unit: str,
    as_of: datetime,
    provenance: Provenance,
    value: str | None = None,
    currency: str | None = None,
    method: str = "reported_balance",
) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "kind": "holding",
        "source_key": source_key,
        "asset_kind": ASSET_KIND,
        "display_name": display_name,
        "instrument": {"symbol": symbol, "chain": chain, "figi": None},
        "quantity": {"amount": quantity, "unit": unit},
        "as_of": iso_utc(as_of),
        "method": method if value is None else "quoted_price",
        "source": provenance.to_dict(),
    }
    if value is not None:
        proposal["value"] = {"amount": value, "currency": currency}
    return proposal


def valuation(*, source_key: str, value: str, currency: str, as_of: datetime, provenance: Provenance) -> dict[str, Any]:
    return {
        "kind": "valuation",
        "source_key": source_key,
        "value": {"amount": value, "currency": currency},
        "as_of": iso_utc(as_of),
        "method": "quoted_price",
        "source": provenance.to_dict(),
    }


def validate_proposal(proposal: dict[str, Any], *, now: datetime, allowed_kinds: Iterable[str] = (ASSET_KIND,)) -> None:
    """Pre-flight check mirroring the design's field rules. Raises ``ValidationError``.

    The host re-validates; this exists so the plugin never submits something
    the host would reject, and so tests can assert provenance is always there.
    """
    assert_no_floats(proposal)
    kind = proposal.get("kind")
    if kind not in ("holding", "valuation"):
        raise ValidationError("kind must be 'holding' or 'valuation'", code="bad_kind")
    key = proposal.get("source_key")
    if not isinstance(key, str) or not _SOURCE_KEY.match(key):
        raise ValidationError("source_key must be 1-256 chars of [A-Za-z0-9:._/-]", code="bad_source_key")
    if kind == "holding":
        if proposal.get("asset_kind") not in set(allowed_kinds):
            raise ValidationError("asset_kind is outside the permission scope", code="bad_asset_kind")
        name = proposal.get("display_name")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 80
            or "\n" in name
            or "\r" in name
            or _URLISH.search(name)
        ):
            raise ValidationError("display_name must be 1-80 chars of plain text without URLs", code="bad_display_name")
    quantity = proposal.get("quantity")
    if quantity is not None:
        _check_amount(quantity.get("amount"), "quantity")
        if not isinstance(quantity.get("unit"), str) or not quantity["unit"]:
            raise ValidationError("quantity.unit missing", code="bad_quantity")
    value = proposal.get("value")
    if value is None and kind == "valuation":
        raise ValidationError("valuation requires a value", code="bad_value")
    if value is not None:
        _check_amount(value.get("amount"), "value", max_scale=CURRENCY_EXPONENTS.get(value.get("currency"), 18))
        if value.get("currency") not in CURRENCY_EXPONENTS:
            raise ValidationError("value.currency not supported", code="bad_value")
    as_of = proposal.get("as_of")
    if not isinstance(as_of, str) or not _ISO_Z.match(as_of):
        raise ValidationError("as_of must be RFC 3339 UTC (Z)", code="bad_as_of")
    moment = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if moment > now + timedelta(minutes=5) or moment < now - timedelta(days=3650):
        raise ValidationError("as_of outside the accepted window", code="bad_as_of")
    _check_provenance(proposal.get("source"))


def _check_amount(amount: Any, name: str, *, max_scale: int = 18) -> None:
    if not isinstance(amount, str) or not _AMOUNT.match(amount):
        raise ValidationError(f"{name}.amount must be a canonical decimal string", code=f"bad_{name}")
    whole, _, frac = amount.partition(".")
    if len(frac) > max_scale or len((whole + frac).lstrip("0")) > 38:
        raise ValidationError(f"{name}.amount exceeds allowed precision", code=f"bad_{name}")


def _check_provenance(source: Any) -> None:
    if not isinstance(source, dict):
        raise ValidationError("source (provenance) is required", code="missing_provenance")
    for field in ("origin", "reference", "fetched_at"):
        if not isinstance(source.get(field), str) or not source[field]:
            raise ValidationError(f"source.{field} is required", code="missing_provenance")
    if len(source["reference"]) > 200 or _URLISH.search(source["reference"]):
        raise ValidationError("source.reference must be <= 200 chars of plain text", code="bad_reference")
    if not _ISO_Z.match(source["fetched_at"]):
        raise ValidationError("source.fetched_at must be RFC 3339 UTC (Z)", code="missing_provenance")


def batches(proposals: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chunk into host-sized batches: <= 200 proposals and <= 50 holdings each."""
    out: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    holdings = 0
    for proposal in proposals:
        is_holding = proposal["kind"] == "holding"
        if len(current) >= MAX_PROPOSALS_PER_CALL or (is_holding and holdings >= MAX_NEW_HOLDINGS_PER_BATCH):
            out.append({"schema": SCHEMA, "proposals": current})
            current, holdings = [], 0
        current.append(proposal)
        holdings += is_holding
    if current:
        out.append({"schema": SCHEMA, "proposals": current})
    return out
