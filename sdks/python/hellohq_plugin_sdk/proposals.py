"""Propose-only writes: typed proposal batches, receipts and refusals.

A Verified plugin that holds ``propose:holdings`` and/or ``propose:valuations``
can *suggest* holdings and dated values. Nothing is written until the person
approves each suggestion in the host UI, and the plugin never learns an item
id, a current value or an approval decision: all it gets back is one receipt
per proposal. Submit with :func:`hellohq_plugin_sdk.host.propose`.

    from datetime import datetime, timezone
    from hellohq_plugin_sdk import host
    from hellohq_plugin_sdk.proposals import Money, Source, Valuation

    now = datetime.now(timezone.utc)
    receipts = host.propose([
        Valuation(
            source_key="uk-lr:uprn:100023336956",
            value=Money("412500", "GBP"),
            as_of=now,
            method="comparable_sales_median",
            source=Source("landregistry.data.gov.uk", "14 sales within 400 m", now),
        ),
    ])

The classes here are a convenience over the wire format
(``hellohq.proposal-batch@1``, ``plugin-protocol/sidecar/host-calls.schema.json``).
The host validates every batch itself and is the only authority on what is
accepted; :mod:`hellohq_plugin_sdk.proposal_validation` can pre-check a batch
client-side but never replaces that. A plain ``dict`` in the wire shape is
accepted everywhere a model is, so a plugin that already builds dicts needs no
conversion.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NamedTuple

from .protocol import ERR_EXECUTION_FAILED, PluginError

#: Value of ``batch["schema"]``.
BATCH_SCHEMA = "hellohq.proposal-batch@1"

KIND_HOLDING = "holding"
KIND_VALUATION = "valuation"

#: Permission ids a proposal kind needs.
PERMISSION_HOLDINGS = "propose:holdings"
PERMISSION_VALUATIONS = "propose:valuations"

#: The closed set of asset kinds a holding may target (``scope.kinds``).
ASSET_KINDS = frozenset(
    {
        "stock_ticker",
        "crypto_ticker",
        "crypto_exchange",
        "home",
        "car",
        "precious_metal",
        "domain",
        "loan_mortgage",
    }
)

#: The closed set of ``method`` values shown to the person in review.
METHODS = frozenset(
    {
        "reported_balance",
        "quoted_price",
        "comparable_sales_median",
        "index_adjusted",
        "statement",
    }
)

#: Host limits per call (docs/plugin/30 section 3.8).
MAX_PROPOSALS_PER_CALL = 200
MAX_HOLDINGS_PER_BATCH = 50
MAX_PAYLOAD_BYTES = 256 * 1024


# ─────────────────────────────────────────────────────────────────────────────
# Receipts
# ─────────────────────────────────────────────────────────────────────────────


class Outcome(enum.StrEnum):
    """What the host did with one proposal. Compares equal to its wire string."""

    #: New pending suggestion.
    QUEUED = "queued"
    #: The same suggestion is already pending.
    DUPLICATE = "duplicate"
    #: Queued, and an older pending suggestion for the same key was superseded.
    SUPERSEDED_OLDER = "superseded_older"
    #: Equal to the latest value the person already approved.
    UNCHANGED = "unchanged"
    #: The person already declined this exact suggestion, or muted the key.
    SUPPRESSED = "suppressed"
    #: Failed host validation; ``Receipt.reason`` says why.
    INVALID = "invalid"
    #: An outcome this SDK does not know (a host newer than the SDK). The
    #: proposal WAS processed; treat it as informational.
    UNKNOWN = "unknown"


class Receipt(NamedTuple):
    """The host's answer for one proposal: ``(index, outcome, reason)``.

    ``index`` is the proposal's position in the submitted batch. ``reason`` is
    a host reason code (for example ``bad_value`` or ``fetched_at_outside_run``)
    and is set only for :attr:`Outcome.INVALID`.
    """

    index: int
    outcome: Outcome
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        """True when the suggestion is now (or already was) awaiting review."""
        return self.outcome in (
            Outcome.QUEUED,
            Outcome.SUPERSEDED_OLDER,
            Outcome.DUPLICATE,
        )


def parse_receipts(raw: Any, *, expected: int | None = None) -> list[Receipt]:
    """Parse the ``receipts`` array of a ``propose_response``.

    Raises :class:`PluginError` (``execution_failed``) when it is not the
    documented shape, or when ``expected`` is given and the count differs: the
    host sends exactly one receipt per proposal, so anything else means the
    plugin and host disagree about the batch and the plugin must not guess.
    """
    if not isinstance(raw, list):
        raise PluginError(
            "propose: host reply carries no receipts list", ERR_EXECUTION_FAILED
        )
    out: list[Receipt] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise PluginError(
                "propose: malformed receipt from host", ERR_EXECUTION_FAILED
            )
        index = item.get("index")
        outcome = item.get("outcome")
        reason = item.get("reason")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or not isinstance(outcome, str)
            or not (reason is None or isinstance(reason, str))
        ):
            raise PluginError(
                "propose: malformed receipt from host", ERR_EXECUTION_FAILED
            )
        try:
            known = Outcome(outcome)
        except ValueError:
            known = Outcome.UNKNOWN
        out.append(Receipt(index, known, reason))
    if expected is not None and len(out) != expected:
        raise PluginError(
            f"propose: host sent {len(out)} receipts for {expected} proposals",
            ERR_EXECUTION_FAILED,
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Refusals (whole-call errors)
# ─────────────────────────────────────────────────────────────────────────────


class ProposeError(PluginError):
    """The host refused a whole ``propose`` call. Nothing was queued.

    ``code`` is the host's ``error_code`` verbatim; ``reason`` is the host's
    reason code when it gave one (for ``bad_request``). The message is the
    host's fixed text and never contains anything the plugin sent. Catch
    :class:`PluginError` to handle every host-call failure alike, or one of the
    subclasses to degrade gracefully.
    """

    #: A later retry (after a pause) can succeed without changing the batch.
    retryable = False

    def __init__(
        self, message: str, code: str = ERR_EXECUTION_FAILED, reason: str | None = None
    ) -> None:
        super().__init__(message, code)
        self.reason = reason


class ProposePermissionDenied(ProposeError):
    """No ``propose:*`` grant, not a Verified plugin, or not granted in this
    workspace. Hosts that cannot serve ``propose`` to this plugin answer this."""


class ProposeUnsupported(ProposeError):
    """The host does not implement ``propose`` (``unknown_method``).

    Degrade by returning the proposals as data instead of submitting them. A
    Tier 1 host that predates ``propose`` does not answer at all (see
    :func:`hellohq_plugin_sdk.host.propose`); declare a ``min_host_version`` in
    the manifest rather than relying on this error.
    """


class ProposeRateLimited(ProposeError):
    """Too many ``propose`` calls (10 a minute, 100 a day, per plugin per
    workspace). The refused call is not counted; try again later."""

    retryable = True


class ProposeQuotaExceeded(ProposeError):
    """The plugin already has (or this batch would leave it with) more than 500
    suggestions awaiting review in this workspace. Retry after the person has
    reviewed some or they expire (14 days)."""

    retryable = True


class ProposeTooLarge(ProposeError):
    """The batch payload is over 256 KiB. Split it."""


class ProposeTooMany(ProposeError):
    """More than 200 proposals, or more than 50 holdings, in one call. Split it."""


class ProposeBadRequest(ProposeError):
    """The batch is not a valid batch as a whole (bad schema id, a host-owned
    field, malformed JSON shape). ``reason`` carries the host's reason code."""


class ProposeWorkspaceUnavailable(ProposeError):
    """No workspace is open, or not the one this run started in."""

    retryable = True


class ProposeHostError(ProposeError):
    """The host failed. The cause is never reported."""


_ERROR_CLASSES: dict[str, type[ProposeError]] = {
    "permission_denied": ProposePermissionDenied,
    "unknown_method": ProposeUnsupported,
    "rate_limit_exceeded": ProposeRateLimited,
    "rate_limited": ProposeRateLimited,  # the Tier-2 spelling
    "quota_exceeded": ProposeQuotaExceeded,
    "too_large": ProposeTooLarge,
    "too_many": ProposeTooMany,
    "bad_request": ProposeBadRequest,
    "workspace_unavailable": ProposeWorkspaceUnavailable,
    "host_error": ProposeHostError,
}


def error_from_response(reply: Mapping[str, Any]) -> ProposeError:
    """Build the :class:`ProposeError` for an error ``propose_response``."""
    text = reply.get("error")
    message = text if isinstance(text, str) and text else "propose refused by host"
    code = reply.get("error_code")
    if not isinstance(code, str) or not code:
        # A host that spells the refusal only in the text, as the Tier-2 bridge does.
        code = (
            "unknown_method" if message.startswith("unknown_method") else "host_error"
        )
    reason = reply.get("reason")
    return _ERROR_CLASSES.get(code, ProposeError)(
        message, code, reason if isinstance(reason, str) else None
    )


# ─────────────────────────────────────────────────────────────────────────────
# Value types
# ─────────────────────────────────────────────────────────────────────────────

_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?\Z")

#: What an amount may be given as. ``float`` is refused at runtime too: a binary
#: float cannot hold a decimal amount exactly.
Amount = str | int | Decimal


def canonical_decimal(amount: Amount) -> str:
    """The canonical wire spelling of an amount: digits and at most one point.

    No sign, exponent or leading zeros, and trailing fractional zeros removed
    (``Decimal("0.51230000")`` -> ``"0.5123"``, ``Decimal("1E+2")`` -> ``"100"``).
    The host canonicalises the same way, so ``"0.5"`` and ``"0.50"`` are one
    amount.

    Raises:
        TypeError: ``amount`` is a ``float`` (or ``bool``, or anything else
            that is not ``str``/``int``/``Decimal``).
        ValueError: it is negative, not finite, or not a plain decimal.
    """
    if isinstance(amount, bool) or not isinstance(amount, (str, int, Decimal)):
        raise TypeError(
            f"amount must be str, int or Decimal, not {type(amount).__name__}"
        )
    if isinstance(amount, str):
        if not _DECIMAL_RE.match(amount) or len(amount) > 100:
            raise ValueError("amount must be a canonical non-negative decimal string")
        text = amount
    else:
        dec = Decimal(amount)
        if not dec.is_finite():
            raise ValueError("amount must be finite")
        if dec < 0:
            raise ValueError("amount must not be negative")
        text = format(dec, "f")
        if text.startswith("-"):  # Decimal("-0")
            text = text[1:]
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_timestamp(moment: datetime | str) -> str:
    """RFC 3339 UTC with a ``Z`` suffix, from an aware ``datetime`` or a string.

    A string is passed through unchanged (the host and
    :mod:`hellohq_plugin_sdk.proposal_validation` check its form). A naive
    ``datetime`` is refused: guessing a zone would put a wrong time in front of
    the person.
    """
    if isinstance(moment, str):
        return moment
    if not isinstance(moment, datetime):
        raise TypeError(
            f"timestamp must be datetime or str, not {type(moment).__name__}"
        )
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (use UTC)")
    utc = moment.astimezone(UTC)
    base = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        base += f".{utc.microsecond:06d}".rstrip("0")
    return base + "Z"


@dataclass(frozen=True)
class Money:
    """A currency amount: ``Money("412500", "GBP")``. ``currency`` is ISO 4217
    or a crypto code the host knows."""

    amount: Amount
    currency: str

    def to_wire(self) -> dict[str, str]:
        return {"amount": canonical_decimal(self.amount), "currency": self.currency}


@dataclass(frozen=True)
class Quantity:
    """A quantity of a unit: ``Quantity("0.5123", "BTC")``."""

    amount: Amount
    unit: str

    def to_wire(self) -> dict[str, str]:
        return {"amount": canonical_decimal(self.amount), "unit": self.unit}


@dataclass(frozen=True)
class Instrument:
    """Optional identifiers of a holding's instrument."""

    symbol: str | None = None
    chain: str | None = None
    figi: str | None = None

    def to_wire(self) -> dict[str, str]:
        return {
            k: v
            for k, v in (
                ("symbol", self.symbol),
                ("chain", self.chain),
                ("figi", self.figi),
            )
            if v is not None
        }


@dataclass(frozen=True)
class Source:
    """Where a proposed value came from.

    ``origin`` is a bare lower-case host name. The host labels it observed only
    if this run actually fetched it through ``host.fetch``; otherwise the
    suggestion is shown as "source not observed". ``fetched_at`` must fall
    inside the run. ``reference`` is plain text shown verbatim (no links).
    """

    origin: str
    reference: str
    fetched_at: datetime | str
    price_origin: str | None = None

    def to_wire(self) -> dict[str, str]:
        out = {
            "origin": self.origin,
            "reference": self.reference,
            "fetched_at": format_timestamp(self.fetched_at),
        }
        if self.price_origin is not None:
            out["price_origin"] = self.price_origin
        return out


@dataclass(frozen=True)
class Holding:
    """Suggest a new holding (needs ``propose:holdings``; ``asset_kind`` must be
    in that permission's ``scope.kinds``)."""

    source_key: str
    asset_kind: str
    display_name: str
    as_of: datetime | str
    source: Source
    quantity: Quantity | None = None
    value: Money | None = None
    instrument: Instrument | None = None
    method: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": KIND_HOLDING,
            "source_key": self.source_key,
            "asset_kind": self.asset_kind,
            "display_name": self.display_name,
        }
        if self.instrument is not None:
            out["instrument"] = self.instrument.to_wire()
        if self.quantity is not None:
            out["quantity"] = self.quantity.to_wire()
        if self.value is not None:
            out["value"] = self.value.to_wire()
        out["as_of"] = format_timestamp(self.as_of)
        if self.method is not None:
            out["method"] = self.method
        out["source"] = self.source.to_wire()
        return out


@dataclass(frozen=True)
class Valuation:
    """Suggest a dated value for a source key the person has linked to an item
    (needs ``propose:valuations``)."""

    source_key: str
    value: Money
    as_of: datetime | str
    source: Source
    quantity: Quantity | None = None
    method: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": KIND_VALUATION,
            "source_key": self.source_key,
            "value": self.value.to_wire(),
        }
        if self.quantity is not None:
            out["quantity"] = self.quantity.to_wire()
        out["as_of"] = format_timestamp(self.as_of)
        if self.method is not None:
            out["method"] = self.method
        out["source"] = self.source.to_wire()
        return out


#: One proposal: a model or its wire ``dict``.
Proposal = Holding | Valuation | Mapping[str, Any]


@dataclass(frozen=True)
class ProposalBatch:
    """An ordered batch of proposals (one ``propose`` call).

    ``Receipt.index`` refers to a position in :attr:`proposals`.
    """

    proposals: Sequence[Proposal]

    def to_wire(self) -> dict[str, Any]:
        """The ``hellohq.proposal-batch@1`` object that goes on the wire."""
        return {
            "schema": BATCH_SCHEMA,
            "proposals": [
                p if isinstance(p, Mapping) else p.to_wire() for p in self.proposals
            ],
        }

    def validate(self, **options: Any):
        """Client-side pre-check; see :func:`hellohq_plugin_sdk.proposal_validation.validate_batch`."""
        from .proposal_validation import validate_batch

        return validate_batch(self.to_wire(), **options)


def batch_to_wire(
    batch: ProposalBatch | Sequence[Proposal] | Mapping[str, Any],
) -> dict[str, Any]:
    """Normalise anything :func:`~hellohq_plugin_sdk.host.propose` accepts to the
    wire batch.

    A ``Mapping`` is taken to already be a batch (``{"schema", "proposals"}``)
    and is returned as a ``dict`` copy, untouched, so a malformed one reaches
    the host, which refuses it. A sequence is a list of proposals, models or
    wire dicts.
    """
    if isinstance(batch, ProposalBatch):
        return batch.to_wire()
    if isinstance(batch, Mapping):
        return dict(batch)
    if isinstance(batch, (str, bytes)) or not isinstance(batch, Sequence):
        raise TypeError(
            "batch must be a ProposalBatch, a sequence of proposals or a batch mapping"
        )
    return ProposalBatch(batch).to_wire()
