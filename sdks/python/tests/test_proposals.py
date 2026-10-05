"""Model helpers: canonical decimals, timestamps, receipts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hellohq_plugin_sdk.proposals import (
    Instrument,
    Money,
    Outcome,
    ProposalBatch,
    Quantity,
    Source,
    Valuation,
    batch_to_wire,
    canonical_decimal,
    format_timestamp,
    parse_receipts,
)


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("0.51230000", "0.5123"),
        ("31744.120", "31744.12"),
        ("412500", "412500"),
        ("0", "0"),
        ("0.000", "0"),
        ("10.0", "10"),
        ("100", "100"),  # trailing zeros of an integer are never trimmed
        (412500, "412500"),
        (0, "0"),
        (Decimal("1E+2"), "100"),
        (Decimal("0.51230000"), "0.5123"),
        (Decimal("1.5E-7"), "0.00000015"),
        (Decimal("-0"), "0"),
        (
            Decimal("123456789012345678901234567890.123456"),
            "123456789012345678901234567890.123456",
        ),
    ],
)
def test_canonical_decimal(amount, expected):
    assert canonical_decimal(amount) == expected


@pytest.mark.parametrize("amount", [0.1, 1.0, True, None, b"1", [1]])
def test_canonical_decimal_refuses_non_decimal_types(amount):
    with pytest.raises(TypeError):
        canonical_decimal(amount)


@pytest.mark.parametrize(
    "amount",
    [
        "-1",
        "+1",
        "1e3",
        "01",
        ".5",
        "5.",
        "1,000",
        " 1",
        "1\n",
        "",
        "NaN",
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        -1,
    ],
)
def test_canonical_decimal_refuses_bad_values(amount):
    with pytest.raises(ValueError):
        canonical_decimal(amount)


def test_format_timestamp():
    assert (
        format_timestamp(datetime(2026, 10, 4, 6, 0, 0, tzinfo=UTC))
        == "2026-10-04T06:00:00Z"
    )
    assert (
        format_timestamp(datetime(2026, 10, 4, 6, 0, 0, 120000, tzinfo=UTC))
        == "2026-10-04T06:00:00.12Z"
    )
    plus8 = timezone(timedelta(hours=8))
    assert (
        format_timestamp(datetime(2026, 10, 4, 14, 0, 0, tzinfo=plus8))
        == "2026-10-04T06:00:00Z"
    )
    assert (
        format_timestamp("2026-10-04T06:00:00Z") == "2026-10-04T06:00:00Z"
    )  # strings pass through


def test_format_timestamp_refuses_naive_datetimes_and_other_types():
    with pytest.raises(ValueError):
        format_timestamp(datetime(2026, 10, 4, 6, 0, 0))
    with pytest.raises(TypeError):
        format_timestamp(1759557600)


def test_optional_fields_are_omitted_not_null():
    when = datetime(2026, 10, 4, tzinfo=UTC)
    wire = Valuation(
        "k", Money(1, "USD"), when, Source("a.example", "ref", when)
    ).to_wire()
    assert set(wire) == {"kind", "source_key", "value", "as_of", "source"}
    assert Instrument(symbol="BTC").to_wire() == {"symbol": "BTC"}
    assert Quantity(Decimal("2"), "BTC").to_wire() == {"amount": "2", "unit": "BTC"}


def test_batch_to_wire_shapes():
    when = datetime(2026, 10, 4, tzinfo=UTC)
    valuation = Valuation("k", Money(1, "USD"), when, Source("a.example", "ref", when))
    expected = {
        "schema": "hellohq.proposal-batch@1",
        "proposals": [valuation.to_wire()],
    }
    assert batch_to_wire([valuation]) == expected
    assert batch_to_wire((valuation,)) == expected
    assert batch_to_wire(ProposalBatch([valuation])) == expected
    assert batch_to_wire(expected) == expected
    assert batch_to_wire(expected) is not expected  # a copy


def test_parse_receipts_expected_count():
    assert parse_receipts([], expected=0) == []
    receipts = parse_receipts(
        [{"index": 0, "outcome": "invalid", "reason": "bad_value"}], expected=1
    )
    assert receipts[0].outcome is Outcome.INVALID
    assert str(Outcome.QUEUED) == "queued"
