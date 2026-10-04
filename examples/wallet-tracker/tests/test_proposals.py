from datetime import UTC, datetime, timedelta

import pytest

from wallet_tracker.errors import ValidationError
from wallet_tracker.proposals import (
    MAX_NEW_HOLDINGS_PER_BATCH,
    MAX_PROPOSALS_PER_CALL,
    SCHEMA,
    Provenance,
    batches,
    btc_source_key,
    holding,
    short_address,
    sol_source_key,
    validate_proposal,
    valuation,
)

NOW = datetime(2026, 10, 4, 6, 0, 0, tzinfo=UTC)
PROV = Provenance("mempool.space", "/api/address/bc1qxyz (confirmed = funded - spent)", "2026-10-04T06:00:00Z")


def good(**over):
    kw = dict(
        source_key="btc:address:bc1qxyz",
        display_name="Bitcoin wallet (bc1qxy...wxyz)",
        symbol="BTC",
        chain="bitcoin",
        quantity="0.51230000",
        unit="BTC",
        as_of=NOW,
        provenance=PROV,
    )
    kw.update(over)
    return holding(**kw)


def test_valid_holding_and_valuation():
    validate_proposal(good(), now=NOW)
    validate_proposal(good(value="30738.28", currency="USD"), now=NOW)
    validate_proposal(
        valuation(source_key="btc:address:x", value="1.00", currency="USD", as_of=NOW, provenance=PROV), now=NOW
    )


def test_every_proposal_carries_provenance():
    p = good(
        value="1.00",
        currency="USD",
        provenance=Provenance("mempool.space", "/r", "2026-10-04T06:00:00Z", price_origin="mempool.space"),
    )
    assert {"origin", "reference", "fetched_at", "price_origin"} <= set(p["source"])
    for field in ("origin", "reference", "fetched_at"):
        bad = good()
        bad["source"][field] = ""
        with pytest.raises(ValidationError) as err:
            validate_proposal(bad, now=NOW)
        assert err.value.code == "missing_provenance"
    bad = good()
    del bad["source"]
    with pytest.raises(ValidationError) as err:
        validate_proposal(bad, now=NOW)
    assert err.value.code == "missing_provenance"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(kind="transaction"), "bad_kind"),
        (lambda p: p.update(source_key="has space"), "bad_source_key"),
        (lambda p: p.update(source_key="a" * 257), "bad_source_key"),
        (lambda p: p.update(asset_kind="home"), "bad_asset_kind"),
        (lambda p: p.update(display_name=""), "bad_display_name"),
        (lambda p: p.update(display_name="x" * 81), "bad_display_name"),
        (lambda p: p.update(display_name="a\nb"), "bad_display_name"),
        (lambda p: p.update(display_name="see https://evil.example"), "bad_display_name"),
        (lambda p: p["quantity"].update(amount="1e5"), "bad_quantity"),
        (lambda p: p["quantity"].update(amount="-1"), "bad_quantity"),
        (lambda p: p["quantity"].update(amount="1." + "0" * 19), "bad_quantity"),
        (lambda p: p["quantity"].update(amount="007"), "bad_quantity"),
        (lambda p: p["quantity"].update(unit=""), "bad_quantity"),
        (lambda p: p.update(as_of="2026-10-04"), "bad_as_of"),
        (lambda p: p.update(as_of="2026-10-04T06:10:00Z"), "bad_as_of"),  # > now + 5 min
        (lambda p: p.update(as_of="2010-01-01T00:00:00Z"), "bad_as_of"),  # > 10 years old
        (lambda p: p["source"].update(reference="x" * 201), "bad_reference"),
        (lambda p: p["source"].update(reference="https://mempool.space/x"), "bad_reference"),
        (lambda p: p["source"].update(fetched_at="yesterday"), "missing_provenance"),
    ],
)
def test_invalid_proposals_are_rejected(mutate, code):
    p = good()
    mutate(p)
    with pytest.raises(ValidationError) as err:
        validate_proposal(p, now=NOW)
    assert err.value.code == code


def test_no_float_money_anywhere():
    p = good()
    p["quantity"]["amount"] = 0.5123
    with pytest.raises(ValidationError) as err:
        validate_proposal(p, now=NOW)
    assert err.value.code == "float_money"
    p = good(value="1.00", currency="USD")
    p["value"]["amount"] = 1.0
    with pytest.raises(ValidationError):
        validate_proposal(p, now=NOW)


def test_value_precision_follows_currency_exponent():
    validate_proposal(good(value="100", currency="JPY"), now=NOW)
    with pytest.raises(ValidationError):
        validate_proposal(good(value="100.5", currency="JPY"), now=NOW)
    with pytest.raises(ValidationError):
        validate_proposal(good(value="1.00", currency="XXX"), now=NOW)


def test_valuation_requires_value():
    p = valuation(source_key="btc:address:x", value="1.00", currency="USD", as_of=NOW, provenance=PROV)
    del p["value"]
    with pytest.raises(ValidationError):
        validate_proposal(p, now=NOW)


def test_batches_respect_host_limits():
    holds = [good(source_key=f"btc:address:a{i}") for i in range(120)]
    out = batches(holds)
    assert [len(b["proposals"]) for b in out] == [50, 50, 20]
    assert all(b["schema"] == SCHEMA for b in out)
    vals = [
        valuation(source_key=f"btc:address:a{i}", value="1.00", currency="USD", as_of=NOW, provenance=PROV)
        for i in range(450)
    ]
    assert [len(b["proposals"]) for b in batches(vals)] == [200, 200, 50]
    assert MAX_NEW_HOLDINGS_PER_BATCH == 50 and MAX_PROPOSALS_PER_CALL == 200
    assert batches([]) == []


def test_source_keys_and_short_address():
    assert btc_source_key("bc1q") == "btc:address:bc1q"
    assert sol_source_key("A") == "sol:address:A"
    assert sol_source_key("A", "M") == "sol:address:A:mint:M"
    assert short_address("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4") == "bc1qw5...f3t4"
    assert short_address("short") == "short"


def test_as_of_window_edges():
    validate_proposal(good(as_of=NOW + timedelta(minutes=5)), now=NOW)
    with pytest.raises(ValidationError):
        validate_proposal(good(as_of=NOW + timedelta(minutes=5, seconds=1)), now=NOW)
