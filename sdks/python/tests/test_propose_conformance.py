"""The SDK's ``propose`` traffic against plugin-protocol's ``host-calls.schema.json``.

Locates the protocol repo as ``$HELLOHQ_PLUGIN_PROTOCOL_DIR``, else a sibling
``plugin-protocol`` checkout of this repository (or of the main checkout when
run from a git worktree). When it is not found the tests skip, unless
``HELLOHQ_REQUIRE_PROTOCOL=1`` (CI), where a missing schema is a failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import hellohq_plugin_sdk.host as host
from hellohq_plugin_sdk.proposal_validation import REASONS, validate_batch
from hellohq_plugin_sdk.proposals import (
    _ERROR_CLASSES,
    ASSET_KINDS,
    METHODS,
    Holding,
    Money,
    Outcome,
    ProposeError,
    Quantity,
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

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_RELATIVE = Path("sidecar") / "host-calls.schema.json"


def _find_schema() -> Path | None:
    candidates: list[Path] = []
    env = os.environ.get("HELLOHQ_PLUGIN_PROTOCOL_DIR")
    if env:
        candidates.append(Path(env))
    repo = Path(__file__).resolve().parents[3]
    candidates += [
        repo.parent / "plugin-protocol",
        repo.parent.parent / "plugin-protocol",
    ]
    for root in candidates:
        if (root / SCHEMA_RELATIVE).is_file():
            return root / SCHEMA_RELATIVE
    return None


@pytest.fixture(scope="module")
def schema() -> dict:
    path = _find_schema()
    if path is None:
        message = "plugin-protocol checkout not found (set HELLOHQ_PLUGIN_PROTOCOL_DIR)"
        if os.environ.get("HELLOHQ_REQUIRE_PROTOCOL") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def conforms(schema):
    cls = jsonschema.Draft202012Validator
    cls.check_schema(schema)
    validator = cls(schema)

    def check(message: dict) -> None:
        errors = sorted(validator.iter_errors(message), key=lambda e: list(e.path))
        assert not errors, "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3])

    def rejected(message: dict) -> bool:
        return not validator.is_valid(message)

    check.rejected = rejected  # type: ignore[attr-defined]
    return check


def _defs(schema: dict) -> dict:
    return schema["$defs"]


def _response_variant(schema: dict) -> dict:
    return next(v for v in schema["oneOf"] if v.get("title") == "propose_response")[
        "properties"
    ]


# ── the SDK's constants agree with the schema ───────────────────────────────


def test_reason_codes_match_the_schema(schema):
    assert REASONS == set(_defs(schema)["reason"]["enum"])


def test_outcomes_match_the_schema(schema):
    wire = set(
        _response_variant(schema)["receipts"]["items"]["properties"]["outcome"]["enum"]
    )
    assert wire == {o.value for o in Outcome if o is not Outcome.UNKNOWN}


def test_error_codes_match_the_schema(schema):
    codes = set(_response_variant(schema)["error_code"]["enum"])
    # The SDK also maps the Tier-2 spelling and `unknown_method`, which the Tier-1 schema does not carry.
    assert codes <= set(_ERROR_CLASSES)
    assert set(_ERROR_CLASSES) - codes == {"rate_limited", "unknown_method"}


def test_asset_kinds_and_methods_match_the_schema(schema):
    proposal = _defs(schema)["proposal"]["properties"]
    assert ASSET_KINDS == {k for k in proposal["asset_kind"]["enum"] if k is not None}
    assert METHODS == {m for m in proposal["method"]["enum"] if m is not None}


# ── what the SDK emits conforms ──────────────────────────────────────────────


def _models():
    holding = Holding(
        source_key="btc:address:abc",
        asset_kind="crypto_ticker",
        display_name="Cold wallet",
        quantity=Quantity("0.5123", "BTC"),
        value=Money("31744.12", "USD"),
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
        "uk-lr:uprn:1",
        Money(412500, "GBP"),
        NOW,
        Source("landregistry.data.gov.uk", "14 sales", FETCHED),
    )
    return holding, valuation


def test_emitted_requests_conform(conforms, monkeypatch):
    holding, valuation = _models()
    for arg in (
        [holding, valuation],
        [holding],
        [],
        batch_wire(holding_wire(), valuation_wire()),
    ):
        count = len(arg["proposals"]) if isinstance(arg, dict) else len(arg)
        reply = {
            "type": "propose_response",
            "seq": 0,
            "receipts": [{"index": i, "outcome": "queued"} for i in range(count)],
        }
        _, sent = exchange(lambda a=arg: host.propose(a), [reply], monkeypatch)
        assert len(sent) == 1
        conforms(sent[0])


def test_fixtures_and_models_agree_with_the_client_validator(monkeypatch):
    holding, valuation = _models()
    assert (
        validate_batch(batch_wire(holding.to_wire(), valuation.to_wire()), now=NOW)
        == []
    )


def test_every_outcome_and_reason_reply_conforms_and_parses(
    schema, conforms, monkeypatch
):
    props = _response_variant(schema)
    for outcome in props["receipts"]["items"]["properties"]["outcome"]["enum"]:
        reply = {
            "type": "propose_response",
            "seq": 0,
            "receipts": [{"index": 0, "outcome": outcome}],
        }
        conforms(reply)
        receipts, _ = exchange(
            lambda: host.propose([valuation_wire()]), [reply], monkeypatch
        )
        assert receipts[0].outcome.value == outcome
    for reason in _defs(schema)["reason"]["enum"]:
        reply = {
            "type": "propose_response",
            "seq": 0,
            "receipts": [{"index": 0, "outcome": "invalid", "reason": reason}],
        }
        conforms(reply)
        receipts, _ = exchange(
            lambda: host.propose([valuation_wire()]), [reply], monkeypatch
        )
        assert receipts[0].reason == reason


def test_every_error_code_reply_conforms_and_maps(schema, conforms, monkeypatch):
    for code in _response_variant(schema)["error_code"]["enum"]:
        reply = {
            "type": "propose_response",
            "seq": 0,
            "error": "Fixed text.",
            "error_code": code,
        }
        if code == "bad_request":
            reply["reason"] = "bad_schema"
        conforms(reply)
        with pytest.raises(ProposeError) as exc:
            exchange(lambda: host.propose([valuation_wire()]), [reply], monkeypatch)
        assert exc.value.code == code
        assert type(exc.value) is _ERROR_CLASSES[code]


# ── the schema and the SDK refuse the same obvious mistakes ──────────────────


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(note="x"),
        lambda p: p.update(as_of="2026-09-30"),
        lambda p: p["value"].update(amount=412500.5),
        lambda p: p["value"].update(amount="-1"),
        lambda p: p["source"].update(origin="HTTPS://X"),
        lambda p: p.update(method="vibes"),
        lambda p: p.update(plugin_id="x"),
        lambda p: p.pop("source"),
    ],
)
def test_schema_rejects_what_the_client_validator_flags(conforms, mutate):
    proposal = valuation_wire()
    mutate(proposal)
    request = {"type": "propose", "seq": 0, "batch": batch_wire(proposal)}
    assert conforms.rejected(request)
    assert validate_batch(request["batch"], now=NOW)  # at least one issue
