"""``host.propose``: the NDJSON wire, every outcome and every refusal."""

from __future__ import annotations

import io
import itertools
import json
import sys
from decimal import Decimal

import pytest

import hellohq_plugin_sdk.host as host
from hellohq_plugin_sdk import PluginError
from hellohq_plugin_sdk.proposals import (
    Holding,
    Instrument,
    Money,
    Outcome,
    ProposalBatch,
    ProposeBadRequest,
    ProposeError,
    ProposeHostError,
    ProposePermissionDenied,
    ProposeQuotaExceeded,
    ProposeRateLimited,
    ProposeTooLarge,
    ProposeTooMany,
    ProposeUnsupported,
    ProposeWorkspaceUnavailable,
    Quantity,
    Receipt,
    Source,
    Valuation,
)

from propose_helpers import (
    FETCHED,
    NOW,
    batch_wire,
    exchange,
    holding_wire,
    valuation_wire,
)


def _ok(*receipts, seq=0):
    return {"type": "propose_response", "seq": seq, "receipts": list(receipts)}


def _err(code, text="fixed text", seq=0, **extra):
    return {
        "type": "propose_response",
        "seq": seq,
        "error": text,
        "error_code": code,
        **extra,
    }


# ── request on the wire ──────────────────────────────────────────────────────


def test_request_envelope_for_a_wire_batch(monkeypatch):
    batch = batch_wire(valuation_wire())
    receipts, sent = exchange(
        lambda: host.propose(batch),
        [_ok({"index": 0, "outcome": "queued"})],
        monkeypatch,
    )
    assert sent == [{"type": "propose", "seq": 0, "batch": batch}]
    assert receipts == [Receipt(0, Outcome.QUEUED, None)]


def test_models_serialise_to_the_wire_shape(monkeypatch):
    holding = Holding(
        source_key="btc:address:abc",
        asset_kind="crypto_ticker",
        display_name="Cold wallet",
        quantity=Quantity(Decimal("0.51230000"), "BTC"),
        value=Money(Decimal("31744.12"), "USD"),
        instrument=Instrument(symbol="BTC", chain="bitcoin"),
        as_of=NOW,
        method="quoted_price",
        source=Source(
            "mempool.space",
            "/api/address/abc/utxo",
            FETCHED,
            price_origin="api.coingecko.com",
        ),
    )
    valuation = Valuation(
        source_key="uk-lr:uprn:1",
        value=Money("412500", "GBP"),
        as_of="2026-09-30T00:00:00Z",
        source=Source("landregistry.data.gov.uk", "14 sales", FETCHED),
    )
    _, sent = exchange(
        lambda: host.propose([holding, valuation]),
        [_ok({"index": 0, "outcome": "queued"}, {"index": 1, "outcome": "queued"})],
        monkeypatch,
    )
    wire = sent[0]["batch"]
    assert wire["schema"] == "hellohq.proposal-batch@1"
    assert wire["proposals"][0] == {
        "kind": "holding",
        "source_key": "btc:address:abc",
        "asset_kind": "crypto_ticker",
        "display_name": "Cold wallet",
        "instrument": {"symbol": "BTC", "chain": "bitcoin"},
        "quantity": {"amount": "0.5123", "unit": "BTC"},
        "value": {"amount": "31744.12", "currency": "USD"},
        "as_of": "2026-10-04T06:00:30Z",
        "method": "quoted_price",
        "source": {
            "origin": "mempool.space",
            "reference": "/api/address/abc/utxo",
            "fetched_at": "2026-10-04T06:00:02Z",
            "price_origin": "api.coingecko.com",
        },
    }
    assert wire["proposals"][1] == {
        "kind": "valuation",
        "source_key": "uk-lr:uprn:1",
        "value": {"amount": "412500", "currency": "GBP"},
        "as_of": "2026-09-30T00:00:00Z",
        "source": {
            "origin": "landregistry.data.gov.uk",
            "reference": "14 sales",
            "fetched_at": "2026-10-04T06:00:02Z",
        },
    }


def test_accepts_proposal_batch_and_mixed_dicts(monkeypatch):
    batch = ProposalBatch([holding_wire(), valuation_wire()])
    _, sent = exchange(
        lambda: host.propose(batch),
        [_ok({"index": 0, "outcome": "queued"}, {"index": 1, "outcome": "queued"})],
        monkeypatch,
    )
    assert [p["kind"] for p in sent[0]["batch"]["proposals"]] == [
        "holding",
        "valuation",
    ]


def test_a_batch_mapping_is_sent_as_given_even_if_malformed(monkeypatch):
    # The host is the authority: the SDK does not "fix" or reject a mapping.
    batch = {"schema": "wrong@9", "proposals": [{"kind": "holding"}], "run_id": "x"}
    with pytest.raises(ProposeBadRequest) as exc:
        exchange(
            lambda: host.propose(batch),
            [_err("bad_request", reason="bad_schema")],
            monkeypatch,
        )
    assert exc.value.reason == "bad_schema"


def test_seq_advances_and_must_match(monkeypatch):
    monkeypatch.setattr(host, "_seq_counter", itertools.count(7))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_ok(seq=3)) + "\n"))
    with pytest.raises(PluginError, match="unexpected host response"):
        host.propose([])


def test_wrong_response_type_is_refused(monkeypatch):
    reply = {"type": "storage_response", "seq": 0, "ok": True}
    with pytest.raises(PluginError, match="unexpected host response"):
        exchange(lambda: host.propose([]), [reply], monkeypatch)


def test_host_closing_stdin_is_an_error(monkeypatch):
    with pytest.raises(PluginError, match="closed stdin"):
        exchange(lambda: host.propose([]), [], monkeypatch)


def test_empty_batch_gets_empty_receipts(monkeypatch):
    receipts, _ = exchange(lambda: host.propose([]), [_ok()], monkeypatch)
    assert receipts == []


def test_non_batch_argument_is_a_type_error(monkeypatch):
    for bad in ("batch", b"batch", 3, None):
        with pytest.raises(TypeError):
            exchange(lambda b=bad: host.propose(b), [], monkeypatch)


# ── every outcome ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "wire,expected",
    [
        ("queued", Outcome.QUEUED),
        ("duplicate", Outcome.DUPLICATE),
        ("superseded_older", Outcome.SUPERSEDED_OLDER),
        ("unchanged", Outcome.UNCHANGED),
        ("suppressed", Outcome.SUPPRESSED),
        ("invalid", Outcome.INVALID),
    ],
)
def test_each_outcome_parses(wire, expected, monkeypatch):
    extra = {"reason": "bad_value"} if wire == "invalid" else {}
    receipts, _ = exchange(
        lambda: host.propose([valuation_wire()]),
        [_ok({"index": 0, "outcome": wire, **extra})],
        monkeypatch,
    )
    assert receipts[0].outcome is expected
    assert receipts[0].outcome == wire  # compares equal to the wire string
    assert receipts[0].reason == extra.get("reason")


def test_receipts_unpack_as_a_tuple(monkeypatch):
    receipts, _ = exchange(
        lambda: host.propose([valuation_wire()]),
        [_ok({"index": 0, "outcome": "invalid", "reason": "fetched_at_outside_run"})],
        monkeypatch,
    )
    index, outcome, reason = receipts[0]
    assert (index, outcome, reason) == (0, Outcome.INVALID, "fetched_at_outside_run")


def test_mixed_receipts_in_order(monkeypatch):
    batch = [valuation_wire(source_key=f"k:{i}") for i in range(4)]
    reply = _ok(
        {"index": 0, "outcome": "queued"},
        {"index": 1, "outcome": "duplicate"},
        {"index": 2, "outcome": "invalid", "reason": "bad_currency"},
        {"index": 3, "outcome": "suppressed"},
    )
    receipts, _ = exchange(lambda: host.propose(batch), [reply], monkeypatch)
    assert [r.index for r in receipts] == [0, 1, 2, 3]
    assert [r.accepted for r in receipts] == [True, True, False, False]


def test_unknown_outcome_from_a_newer_host_does_not_crash(monkeypatch):
    receipts, _ = exchange(
        lambda: host.propose([valuation_wire()]),
        [_ok({"index": 0, "outcome": "deferred"})],
        monkeypatch,
    )
    assert receipts[0].outcome is Outcome.UNKNOWN


@pytest.mark.parametrize(
    "reply",
    [
        {"type": "propose_response", "seq": 0},  # no receipts, no error
        {"type": "propose_response", "seq": 0, "receipts": "queued"},
        _ok({"index": 0}),  # no outcome
        _ok({"index": "0", "outcome": "queued"}),
        _ok({"index": True, "outcome": "queued"}),
        _ok({"index": -1, "outcome": "queued"}),
        _ok({"index": 0, "outcome": "queued", "reason": 3}),
        _ok("queued"),
        _ok(),  # one proposal sent, zero receipts
        _ok(
            {"index": 0, "outcome": "queued"}, {"index": 1, "outcome": "queued"}
        ),  # too many
    ],
)
def test_malformed_replies_are_refused(reply, monkeypatch):
    with pytest.raises(PluginError) as exc:
        exchange(lambda: host.propose([valuation_wire()]), [reply], monkeypatch)
    assert not isinstance(exc.value, ProposeError)
    assert exc.value.code == "execution_failed"


# ── every refusal ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code,cls,retryable",
    [
        ("permission_denied", ProposePermissionDenied, False),
        ("unknown_method", ProposeUnsupported, False),
        ("rate_limit_exceeded", ProposeRateLimited, True),
        ("rate_limited", ProposeRateLimited, True),  # the Tier-2 spelling
        ("quota_exceeded", ProposeQuotaExceeded, True),
        ("too_large", ProposeTooLarge, False),
        ("too_many", ProposeTooMany, False),
        ("bad_request", ProposeBadRequest, False),
        ("workspace_unavailable", ProposeWorkspaceUnavailable, True),
        ("host_error", ProposeHostError, False),
    ],
)
def test_each_refusal_maps_to_a_typed_error(code, cls, retryable, monkeypatch):
    with pytest.raises(cls) as exc:
        exchange(
            lambda: host.propose([valuation_wire()]),
            [_err(code, "Fixed host text.")],
            monkeypatch,
        )
    assert type(exc.value) is cls
    assert exc.value.code == code
    assert exc.value.message == "Fixed host text."
    assert exc.value.retryable is retryable
    assert isinstance(exc.value, ProposeError)
    assert isinstance(exc.value, PluginError)  # `except PluginError` keeps working


def test_bad_request_carries_the_reason(monkeypatch):
    with pytest.raises(ProposeBadRequest) as exc:
        exchange(
            lambda: host.propose([valuation_wire()]),
            [
                _err(
                    "bad_request",
                    "The proposal batch is not valid.",
                    reason="host_field_supplied",
                )
            ],
            monkeypatch,
        )
    assert exc.value.reason == "host_field_supplied"


def test_unknown_error_code_is_a_plain_propose_error(monkeypatch):
    with pytest.raises(ProposeError) as exc:
        exchange(
            lambda: host.propose([valuation_wire()]), [_err("future_code")], monkeypatch
        )
    assert type(exc.value) is ProposeError
    assert exc.value.code == "future_code"


def test_unknown_method_spelled_only_in_the_text(monkeypatch):
    # The Tier-2 bridge answers {"ok": false, "error": "unknown_method:propose"}.
    reply = {"type": "propose_response", "seq": 0, "error": "unknown_method:propose"}
    with pytest.raises(ProposeUnsupported) as exc:
        exchange(lambda: host.propose([valuation_wire()]), [reply], monkeypatch)
    assert exc.value.code == "unknown_method"


def test_error_without_any_code_is_a_host_error(monkeypatch):
    reply = {"type": "propose_response", "seq": 0, "error": "boom"}
    with pytest.raises(ProposeHostError):
        exchange(lambda: host.propose([valuation_wire()]), [reply], monkeypatch)


def test_graceful_degradation_pattern(monkeypatch):
    """What a plugin does: catch the refusal, keep the proposals as data."""
    proposals = [valuation_wire()]

    def run():
        try:
            return {"status": "submitted", "receipts": host.propose(proposals)}
        except ProposeUnsupported:
            return {"status": "host_unsupported", "proposals": proposals}
        except ProposePermissionDenied:
            return {"status": "permission_denied", "proposals": proposals}

    unsupported, _ = exchange(run, [_err("unknown_method")], monkeypatch)
    assert unsupported["status"] == "host_unsupported"
    denied, _ = exchange(run, [_err("permission_denied")], monkeypatch)
    assert denied["status"] == "permission_denied"


def test_money_refuses_floats():
    with pytest.raises(TypeError):
        Money(0.1, "USD").to_wire()
    with pytest.raises(TypeError):
        Quantity(1.5, "BTC").to_wire()
