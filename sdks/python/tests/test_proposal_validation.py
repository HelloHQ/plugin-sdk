"""Client-side pre-check: it mirrors the host's rules and reason codes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hellohq_plugin_sdk.proposal_validation import (
    REASONS,
    Issue,
    contains_url_like_text,
    is_plain_display_text,
    parse_decimal,
    parse_rfc3339_utc,
    validate_batch,
    validate_proposal,
)

from propose_helpers import NOW, RUN_START, batch_wire, holding_wire, valuation_wire

OPTS = {"now": NOW, "allowed_kinds": {"crypto_ticker"}, "run_start": RUN_START}


def _reason(proposal, **opts):
    issues = validate_batch(batch_wire(proposal), **{**OPTS, **opts})
    assert len(issues) <= 1
    return issues[0].reason if issues else None


def test_valid_batch_has_no_issues():
    batch = batch_wire(holding_wire(), valuation_wire())
    assert (
        validate_batch(
            batch, granted={"propose:holdings", "propose:valuations"}, **OPTS
        )
        == []
    )
    assert validate_proposal(holding_wire(), **OPTS) is None


def _drop(path):
    def mutate(p):
        node = p
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


def _set(path, value):
    def mutate(p):
        node = p
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


# (id, base, mutation, expected reason). Each row is one rule from the host's validator.
CASES = [
    ("bad-kind", holding_wire, _set(["kind"], "transaction"), "bad_kind"),
    ("unknown-field", holding_wire, _set(["note"], "x"), "unknown_field"),
    (
        "holding-field-on-valuation",
        valuation_wire,
        _set(["asset_kind"], "home"),
        "field_not_allowed_for_kind",
    ),
    (
        "holding-field-null-on-valuation-ok",
        valuation_wire,
        _set(["asset_kind"], None),
        None,
    ),
    ("missing-source-key", holding_wire, _drop(["source_key"]), "missing_field"),
    (
        "bad-source-key-chars",
        holding_wire,
        _set(["source_key"], "has space"),
        "bad_source_key",
    ),
    (
        "bad-source-key-length",
        holding_wire,
        _set(["source_key"], "a" * 257),
        "bad_source_key",
    ),
    ("source-key-length-ok", holding_wire, _set(["source_key"], "a" * 256), None),
    ("missing-asset-kind", holding_wire, _drop(["asset_kind"]), "missing_field"),
    ("bad-asset-kind", holding_wire, _set(["asset_kind"], "yacht"), "bad_asset_kind"),
    (
        "asset-kind-outside-scope",
        holding_wire,
        _set(["asset_kind"], "home"),
        "asset_kind_not_allowed",
    ),
    ("missing-display-name", holding_wire, _drop(["display_name"]), "missing_field"),
    (
        "empty-display-name",
        holding_wire,
        _set(["display_name"], ""),
        "bad_display_name",
    ),
    (
        "long-display-name",
        holding_wire,
        _set(["display_name"], "x" * 81),
        "bad_display_name",
    ),
    ("display-name-80-ok", holding_wire, _set(["display_name"], "x" * 80), None),
    (
        "display-name-padded",
        holding_wire,
        _set(["display_name"], " Wallet"),
        "bad_display_name",
    ),
    (
        "display-name-newline",
        holding_wire,
        _set(["display_name"], "Wal\nlet"),
        "bad_display_name",
    ),
    (
        "display-name-url",
        holding_wire,
        _set(["display_name"], "see https://x.example"),
        "bad_display_name",
    ),
    (
        "display-name-bare-domain",
        holding_wire,
        _set(["display_name"], "Wallet at example.com"),
        "bad_display_name",
    ),
    (
        "display-name-zero-width",
        holding_wire,
        _set(["display_name"], "Wal​let"),
        "bad_display_name",
    ),
    (
        "bad-instrument-key",
        holding_wire,
        _set(["instrument"], {"isin": "x"}),
        "bad_instrument",
    ),
    (
        "bad-instrument-chain",
        holding_wire,
        _set(["instrument", "chain"], "Bitcoin"),
        "bad_instrument",
    ),
    (
        "bad-instrument-figi",
        holding_wire,
        _set(["instrument", "figi"], "abc"),
        "bad_instrument",
    ),
    ("quantity-float", holding_wire, _set(["quantity", "amount"], 0.5), "bad_quantity"),
    (
        "quantity-negative",
        holding_wire,
        _set(["quantity", "amount"], "-1"),
        "bad_quantity",
    ),
    (
        "quantity-leading-zero",
        holding_wire,
        _set(["quantity", "amount"], "01"),
        "bad_quantity",
    ),
    (
        "quantity-exponent",
        holding_wire,
        _set(["quantity", "amount"], "1e3"),
        "bad_quantity",
    ),
    (
        "quantity-scale-19",
        holding_wire,
        _set(["quantity", "amount"], "0." + "1" * 19),
        "bad_quantity",
    ),
    (
        "quantity-scale-18-ok",
        holding_wire,
        _set(["quantity", "amount"], "0." + "1" * 18),
        None,
    ),
    (
        "quantity-trailing-zeros-not-scale",
        holding_wire,
        _set(["quantity", "amount"], "0.1" + "0" * 30),
        None,
    ),
    (
        "quantity-39-digits",
        holding_wire,
        _set(["quantity", "amount"], "1" * 39),
        "bad_quantity",
    ),
    (
        "quantity-38-digits-ok",
        holding_wire,
        _set(["quantity", "amount"], "1" * 38),
        None,
    ),
    (
        "quantity-bad-unit",
        holding_wire,
        _set(["quantity", "unit"], "has space"),
        "bad_quantity",
    ),
    (
        "quantity-extra-key",
        holding_wire,
        _set(["quantity", "scale"], 2),
        "bad_quantity",
    ),
    ("value-float", valuation_wire, _set(["value", "amount"], 412500.0), "bad_value"),
    ("value-negative", valuation_wire, _set(["value", "amount"], "-5"), "bad_value"),
    (
        "value-bad-currency",
        valuation_wire,
        _set(["value", "currency"], "gbp"),
        "bad_currency",
    ),
    (
        "value-missing-currency",
        valuation_wire,
        _drop(["value", "currency"]),
        "bad_currency",
    ),
    ("valuation-without-value", valuation_wire, _drop(["value"]), "missing_field"),
    ("holding-without-value-ok", holding_wire, _drop(["value"]), None),
    ("missing-as-of", holding_wire, _drop(["as_of"]), "missing_field"),
    ("date-only-as-of", valuation_wire, _set(["as_of"], "2026-09-30"), "bad_as_of"),
    (
        "offset-as-of",
        valuation_wire,
        _set(["as_of"], "2026-09-30T00:00:00+00:00"),
        "bad_as_of",
    ),
    (
        "lowercase-z-as-of",
        valuation_wire,
        _set(["as_of"], "2026-09-30T00:00:00z"),
        "bad_as_of",
    ),
    (
        "impossible-date-as-of",
        valuation_wire,
        _set(["as_of"], "2026-02-30T00:00:00Z"),
        "bad_as_of",
    ),
    (
        "trailing-newline-as-of",
        valuation_wire,
        _set(["as_of"], "2026-09-30T00:00:00Z\n"),
        "bad_as_of",
    ),
    (
        "as-of-too-far-ahead",
        valuation_wire,
        _set(["as_of"], "2026-10-04T06:06:00Z"),
        "as_of_in_future",
    ),
    (
        "as-of-within-tolerance",
        valuation_wire,
        _set(["as_of"], "2026-10-04T06:05:00Z"),
        None,
    ),
    (
        "as-of-too-old",
        valuation_wire,
        _set(["as_of"], "2016-10-03T00:00:00Z"),
        "as_of_too_old",
    ),
    (
        "as-of-ten-years-ok",
        valuation_wire,
        _set(["as_of"], "2016-10-05T00:00:00Z"),
        None,
    ),
    ("bad-method", valuation_wire, _set(["method"], "vibes"), "bad_method"),
    ("null-method-ok", valuation_wire, _set(["method"], None), None),
    ("missing-source", valuation_wire, _drop(["source"]), "missing_field"),
    ("source-extra-key", valuation_wire, _set(["source", "url"], "x"), "bad_source"),
    (
        "source-origin-url",
        valuation_wire,
        _set(["source", "origin"], "https://a.example"),
        "bad_source",
    ),
    (
        "source-origin-uppercase",
        valuation_wire,
        _set(["source", "origin"], "Example.com"),
        "bad_source",
    ),
    (
        "source-bad-price-origin",
        valuation_wire,
        _set(["source", "price_origin"], "a b"),
        "bad_source",
    ),
    (
        "source-missing-origin",
        valuation_wire,
        _drop(["source", "origin"]),
        "missing_field",
    ),
    (
        "reference-too-long",
        valuation_wire,
        _set(["source", "reference"], "x" * 201),
        "bad_reference",
    ),
    (
        "reference-url",
        valuation_wire,
        _set(["source", "reference"], "see https://a.example/x"),
        "bad_reference",
    ),
    (
        "reference-www",
        valuation_wire,
        _set(["source", "reference"], "see www.example"),
        "bad_reference",
    ),
    (
        "reference-may-cite-a-host",
        valuation_wire,
        _set(["source", "reference"], "landregistry.data.gov.uk: 14 sales"),
        None,
    ),
    (
        "fetched-at-date-only",
        valuation_wire,
        _set(["source", "fetched_at"], "2026-10-04"),
        "bad_fetched_at",
    ),
    (
        "fetched-before-run",
        valuation_wire,
        _set(["source", "fetched_at"], "2026-10-04T05:00:00Z"),
        "fetched_at_outside_run",
    ),
    (
        "fetched-after-now",
        valuation_wire,
        _set(["source", "fetched_at"], "2026-10-04T06:01:00Z"),
        "fetched_at_outside_run",
    ),
    (
        "fetched-skew-ok",
        valuation_wire,
        _set(["source", "fetched_at"], "2026-10-04T06:00:34Z"),
        None,
    ),
]


@pytest.mark.parametrize("name,base,mutate,expected", CASES, ids=[c[0] for c in CASES])
def test_proposal_rules(name, base, mutate, expected):
    proposal = base()
    mutate(proposal)
    assert _reason(proposal) == expected
    if expected is not None:
        assert expected in REASONS


def test_proposal_that_is_not_an_object():
    assert validate_batch(
        {"schema": "hellohq.proposal-batch@1", "proposals": ["x"]}, **OPTS
    ) == [Issue(0, "invalid")]


def test_permission_per_kind():
    granted = {"propose:valuations"}
    issues = validate_batch(
        batch_wire(holding_wire(), valuation_wire()), granted=granted, **OPTS
    )
    assert issues == [Issue(0, "permission_denied")]
    assert validate_batch(batch_wire(valuation_wire()), granted=set(), **OPTS) == [
        Issue(0, "permission_denied")
    ]
    # No `granted` given: the check is skipped.
    assert validate_batch(batch_wire(valuation_wire()), **OPTS) == []


def test_scope_is_skipped_unless_given():
    home = holding_wire(asset_kind="home")
    assert validate_batch(batch_wire(home), now=NOW, run_start=RUN_START) == []
    assert validate_batch(batch_wire(home), now=NOW, allowed_kinds={"home"}) == []


def test_run_window_is_skipped_unless_given():
    old = valuation_wire()
    old["source"]["fetched_at"] = "2020-01-01T00:00:00Z"
    assert validate_batch(batch_wire(old), now=NOW) == []
    assert validate_batch(batch_wire(old), now=NOW, run_start=RUN_START) == [
        Issue(0, "fetched_at_outside_run")
    ]


def test_first_failing_reason_per_proposal_in_host_order():
    # Host order: kind, permission, field names, source_key, holding fields, quantity, value, as_of, method, source.
    proposal = valuation_wire(source_key="bad key", method="vibes")
    assert _reason(proposal) == "bad_source_key"
    proposal = valuation_wire(method="vibes", as_of="nope")
    assert _reason(proposal) == "bad_as_of"
    # Each proposal is judged on its own.
    issues = validate_batch(
        batch_wire(valuation_wire(), valuation_wire(method="vibes"), valuation_wire()),
        **OPTS,
    )
    assert issues == [Issue(1, "bad_method")]


# ── whole-batch refusals ─────────────────────────────────────────────────────


def test_batch_level_refusals():
    good = batch_wire(valuation_wire())
    assert validate_batch("nope", **OPTS) == [Issue(None, "invalid")]
    assert validate_batch({**good, "extra": 1}, **OPTS) == [
        Issue(None, "unknown_field")
    ]
    assert validate_batch({"proposals": good["proposals"]}, **OPTS) == [
        Issue(None, "bad_schema")
    ]
    assert validate_batch({**good, "schema": "hellohq.proposal-batch@2"}, **OPTS) == [
        Issue(None, "bad_schema")
    ]
    assert validate_batch({"schema": good["schema"], "proposals": {}}, **OPTS) == [
        Issue(None, "invalid")
    ]


def test_host_owned_fields_refuse_the_whole_batch_at_any_depth():
    for name in (
        "plugin_id",
        "run_id",
        "content_hash",
        "source_observed",
        "dedup_key",
        "host_observed_origins",
    ):
        top = valuation_wire(**{name: "x"})
        assert validate_batch(batch_wire(top), **OPTS) == [
            Issue(None, "host_field_supplied")
        ], name
        nested = valuation_wire()
        nested["source"][name] = "x"
        assert validate_batch(batch_wire(nested), **OPTS) == [
            Issue(None, "host_field_supplied")
        ], name


def test_count_limits():
    two_hundred = batch_wire(*[valuation_wire(source_key=f"k:{i}") for i in range(200)])
    assert validate_batch(two_hundred, **OPTS) == []
    over = batch_wire(*[valuation_wire(source_key=f"k:{i}") for i in range(201)])
    assert validate_batch(over, **OPTS) == [Issue(None, "too_many")]
    fifty = batch_wire(*[holding_wire(source_key=f"k:{i}") for i in range(50)])
    assert validate_batch(fifty, **OPTS) == []
    fifty_one = batch_wire(*[holding_wire(source_key=f"k:{i}") for i in range(51)])
    assert validate_batch(fifty_one, **OPTS) == [Issue(None, "too_many")]


def test_payload_size_limit():
    padded = batch_wire(valuation_wire())
    padded["proposals"][0]["source"]["reference"] = "x" * 300_000
    assert validate_batch(padded, **OPTS) == [Issue(None, "too_large")]
    # Just under the cap is not too_large (it is a bad_reference, a different rule).
    padded["proposals"][0]["source"]["reference"] = "x" * 250_000
    assert validate_batch(padded, **OPTS) == [Issue(0, "bad_reference")]


def test_hostile_shapes_never_raise():
    cyclic: dict = {"schema": "hellohq.proposal-batch@1"}
    cyclic["proposals"] = [cyclic]
    deep: object = "x"
    for _ in range(40):
        deep = [deep]
    for hostile in (
        cyclic,
        {"schema": 1, "proposals": deep},
        {1: 2},
        {"proposals": [float("nan")]},
        object(),
    ):
        assert validate_batch(hostile, **OPTS)  # at least one issue, no exception


# ── helpers ──────────────────────────────────────────────────────────────────


def test_parse_decimal():
    assert parse_decimal("0.51230000") == "0.5123"
    assert parse_decimal("5") == "5"
    assert parse_decimal("5.000") == "5"
    assert parse_decimal(5) is None
    assert parse_decimal("1" * 101) is None


def test_parse_rfc3339_utc():
    assert parse_rfc3339_utc("2026-10-04T06:00:00Z") == datetime(
        2026, 10, 4, 6, 0, tzinfo=UTC
    )
    assert parse_rfc3339_utc("2026-10-04T06:00:00.123456789Z") == datetime(
        2026, 10, 4, 6, 0, 0, 123456, tzinfo=UTC
    )
    for bad in (
        "2026-10-04T06:00:00",
        "2026-13-04T06:00:00Z",
        "2026-10-04T24:00:00Z",
        "2026-10-04T06:00:60Z",
        "0000-01-01T00:00:00Z",
        5,
    ):
        assert parse_rfc3339_utc(bad) is None


def test_plain_text_and_links():
    assert is_plain_display_text("Cold wallet (bc1q...0wlh)")
    assert not is_plain_display_text("a\tb")
    assert not is_plain_display_text("a‮b")  # bidi override
    assert not is_plain_display_text("a\ud800")  # lone surrogate
    assert contains_url_like_text("javascript:alert(1)", include_bare_domains=False)
    assert contains_url_like_text("WWW.example", include_bare_domains=False)
    assert contains_url_like_text("pay example.com", include_bare_domains=True)
    assert not contains_url_like_text("pay example.com", include_bare_domains=False)
    assert not contains_url_like_text("Wallet 12.5 BTC", include_bare_domains=True)


def test_now_defaults_to_the_clock():
    fresh = valuation_wire()
    fresh["as_of"] = (datetime.now(UTC) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    fresh["source"]["fetched_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert validate_batch(batch_wire(fresh)) == []
