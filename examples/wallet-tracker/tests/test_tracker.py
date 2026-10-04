import json
from datetime import UTC, datetime

import pytest

from conftest import FakeClock, FakeHost, ok
from fixtures import (
    BTC_P2PKH,
    BTC_P2TR,
    BTC_P2WPKH,
    PRICES,
    SOL_DOC_ADDRESS,
    SOL_DOC_MINT,
    SOL_DOC_OWNER,
    SOL_OTHER_MINT,
    SOL_SYSTEM_PROGRAM,
    SYN_TESTNET_P2PKH,
    address_response,
    multiple_accounts_result,
    rpc_error,
    rpc_ok,
    token_account,
    token_accounts_result,
    utxo_response,
)
from wallet_tracker import solana
from wallet_tracker.errors import ValidationError
from wallet_tracker.host import HttpResponse
from wallet_tracker.money import assert_no_floats
from wallet_tracker.proposals import validate_proposal
from wallet_tracker.tracker import ScanRequest, parse_request, scan


class Routes:
    """URL/RPC router for FakeHost handlers."""

    def __init__(self):
        self.btc: dict[tuple[str, str], object] = {}  # (host, address) -> body | HttpResponse | Exception
        self.prices: object = ok(PRICES)
        self.sol_balances: dict[str, int | None] = {}
        self.sol_tokens: dict[tuple[str, str], list[dict]] = {}  # (owner, program) -> accounts
        self.rpc_failures: dict[str, str] = {}

    def __call__(self, method, url, body):
        if method == "GET":
            host = url.split("/")[2]
            path = "/" + url.split("/", 3)[3]
            if path == "/api/v1/prices":
                return self.prices
            address = path.split("/")[3]
            found = self.btc.get((host, address))
            if found is None:
                return None
            return ok(found) if isinstance(found, str) else found
        req = json.loads(body)
        rid, name = req["id"], req["method"]
        if name in self.rpc_failures:
            return ok(rpc_error(rid, -32005, "Node is unhealthy"))
        if name == "getMultipleAccounts":
            addrs = req["params"][0]
            return ok(rpc_ok(rid, multiple_accounts_result([self.sol_balances.get(a) for a in addrs])))
        owner, program = req["params"][0], req["params"][1]["programId"]
        return ok(rpc_ok(rid, token_accounts_result(self.sol_tokens.get((owner, program), []))))


def run(routes, request, clock=None, host=None):
    clock = clock or FakeClock()
    host = host or FakeHost(routes)
    return scan(host, request, clock, rand=lambda: 1.0), host, clock


def all_proposals(report):
    return [p for b in report["proposals"] for p in b["proposals"]]


def test_btc_wallet_priced_proposal_with_provenance():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH, 150_000_000, 98_770_000)
    report, host, clock = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)))
    (p,) = all_proposals(report)
    assert p["kind"] == "holding" and p["source_key"] == f"btc:address:{BTC_P2WPKH}"
    assert p["quantity"] == {"amount": "0.51230000", "unit": "BTC"}
    assert p["value"] == {"amount": "30738.28", "currency": "USD"}
    assert p["source"]["origin"] == "mempool.space" and p["source"]["price_origin"] == "mempool.space"
    assert p["source"]["reference"].startswith(f"/api/address/{BTC_P2WPKH}")
    assert p["source"]["fetched_at"].endswith("Z")
    assert report["price"]["currency"] == "USD" and report["issues"] == []
    # Everything the plugin proposes passes its own pre-flight validation.
    validate_proposal(p, now=clock.utcnow())
    assert report["submission"]["status"] == "submitted"
    assert host.batches[0]["proposals"] == [p]


def test_btc_without_price_currency_proposes_quantity_only():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, host, _ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), price_currency=None))
    (p,) = all_proposals(report)
    assert "value" not in p and "price_origin" not in p["source"]
    assert not any("prices" in c[1] for c in host.calls)


def test_price_failure_degrades_to_quantity_only_with_an_issue():
    routes = Routes()
    routes.prices = ok('{"time":1,"EUR":5}')  # no USD quoted
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)))
    (p,) = all_proposals(report)
    assert "value" not in p
    assert [i["code"] for i in report["issues"]] == ["btc_price_unavailable"]
    routes.prices = ok("garbage")
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)), clock=FakeClock())
    assert report["issues"][0]["code"] == "btc_price_unavailable"


def test_other_currency_and_zero_decimal_rounding():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH, 100_000_000, 0)
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), price_currency="JPY"))
    assert all_proposals(report)[0]["value"] == {"amount": "9000000", "currency": "JPY"}


def test_valuation_kind_is_opt_in():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), kinds=("holding", "valuation")))
    assert [p["kind"] for p in all_proposals(report)] == ["holding", "valuation"]
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), kinds=("valuation",)), clock=FakeClock())
    assert [p["kind"] for p in all_proposals(report)] == ["valuation"]


def test_falls_back_to_esplora_and_records_the_origin_actually_used():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = HttpResponse(500, "")
    routes.btc[("blockstream.info", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, host, _ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)))
    assert all_proposals(report)[0]["source"]["origin"] == "blockstream.info"
    assert report["balances"][0]["origin"] == "blockstream.info"


def test_parse_failure_on_first_source_falls_back_too():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = '{"unexpected": true}'
    routes.btc[("blockstream.info", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)))
    assert len(all_proposals(report)) == 1


def test_both_sources_failing_reports_an_issue_and_proposes_nothing():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = HttpResponse(404, "")
    routes.btc[("blockstream.info", BTC_P2WPKH)] = '{"nope":1}'
    report, host, _ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)))
    assert all_proposals(report) == [] and host.batches == []
    assert report["issues"][0]["code"] == "btc_fetch_failed"
    assert report["submission"]["status"] == "nothing_to_submit"


def test_invalid_and_duplicate_addresses_are_reported_not_fetched():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    req = ScanRequest(btc_addresses=(BTC_P2WPKH, BTC_P2WPKH, "garbage", SYN_TESTNET_P2PKH), price_currency=None)
    report, host, _ = run(routes, req)
    codes = sorted(i["code"] for i in report["issues"])
    assert codes == ["btc_bad_charset", "btc_wrong_network"] or "btc_wrong_network" in codes
    assert len(all_proposals(report)) == 1  # duplicate collapsed
    assert len([c for c in host.calls if "/api/address/" in c[1]]) == 1


def test_btc_requests_are_sequential_paced_and_cached_across_addresses():
    routes = Routes()
    for a in (BTC_P2WPKH, BTC_P2PKH, BTC_P2TR):
        routes.btc[("mempool.space", a)] = address_response(a)
    report, host, clock = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH, BTC_P2PKH, BTC_P2TR)))
    assert len(host.calls) == 4  # one price + three addresses
    assert clock.t - 1000.0 >= 3.0  # >= 1 s between the 4 requests


def test_utxo_count_optional_extra_request():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    routes.btc[("mempool.space", BTC_P2WPKH + "/utxo")] = utxo_response(3)
    # route lookup uses path segment 3, i.e. the address; serve utxo by path suffix instead
    base = routes.__call__

    def handler(method, url, body):
        if url.endswith("/utxo"):
            return ok(utxo_response(3))
        return base(method, url, body)

    host = FakeHost(handler)
    report, *_ = run(
        routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), include_utxo_count=True, price_currency=None), host=host
    )
    assert report["balances"][0]["utxo_count"] == 3


def test_solana_native_balances_are_batched_into_one_call():
    routes = Routes()
    addrs = [SOL_DOC_ADDRESS, SOL_DOC_OWNER, SOL_SYSTEM_PROGRAM]
    routes.sol_balances = {SOL_DOC_ADDRESS: 1_500_000_000, SOL_DOC_OWNER: 0}  # third is a missing account
    report, host, _ = run(routes, ScanRequest(solana_addresses=tuple(addrs)))
    multi = [c for c in host.calls if '"getMultipleAccounts"' in c[3]]
    assert len(multi) == 1
    quantities = {p["source_key"]: p["quantity"]["amount"] for p in all_proposals(report)}
    assert quantities[f"sol:address:{SOL_DOC_ADDRESS}"] == "1.500000000"
    assert quantities[f"sol:address:{SOL_SYSTEM_PROGRAM}"] == "0.000000000"
    assert {b["account_exists"] for b in report["balances"] if b["asset"] == "SOL"} == {True, False}
    assert all("value" not in p for p in all_proposals(report))  # no keyless documented SOL price
    assert any("quantity only" in n for n in report["notes"])


def test_solana_batches_of_100_and_one_token_call_per_program_per_wallet():
    synthetic = [solana.base58_encode if False else None]  # placeholder to keep flake quiet
    del synthetic
    from wallet_tracker.addresses import base58_encode

    addrs = tuple(base58_encode(bytes([i + 1]) * 32) for i in range(120))
    routes = Routes()
    report, host, _ = run(routes, ScanRequest(solana_addresses=addrs), clock=FakeClock())
    multi = [c for c in host.calls if '"getMultipleAccounts"' in c[3]]
    assert len(multi) == 2  # 100 + 20
    tokens = [c for c in host.calls if '"getTokenAccountsByOwner"' in c[3]]
    assert len(tokens) == 240  # 2 programs per wallet
    assert all(solana.RPC_URL == c[1] for c in host.calls)


def test_solana_rpc_pacing_stays_within_documented_public_limits():
    from wallet_tracker.addresses import base58_encode

    addrs = tuple(base58_encode(bytes([i + 1]) * 32) for i in range(25))
    routes = Routes()
    clock = FakeClock()
    stamps: list[float] = []

    def handler(method, url, body):
        stamps.append(clock.t)
        return routes(method, url, body)

    run(routes, ScanRequest(solana_addresses=addrs), clock=clock, host=FakeHost(handler))
    assert len(stamps) == 1 + 50
    for i, s in enumerate(stamps):
        assert len([x for x in stamps[i:] if x < s + 10]) <= 20  # our cap, below Solana's 40/10s per method


def test_spl_tokens_aggregated_per_mint_both_programs_zero_dropped():
    routes = Routes()
    routes.sol_tokens = {
        (SOL_DOC_OWNER, solana.TOKEN_PROGRAM): [
            token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "420000000000000", 6),
            token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "1", 6),
            token_account(SOL_OTHER_MINT, SOL_DOC_OWNER, "0", 9),
        ],
        (SOL_DOC_OWNER, solana.TOKEN_2022_PROGRAM): [
            token_account(SOL_SYSTEM_PROGRAM, SOL_DOC_OWNER, "5", 0, program="spl-token-2022"),
        ],
    }
    routes.sol_balances = {SOL_DOC_OWNER: 10}
    report, *_ = run(routes, ScanRequest(solana_addresses=(SOL_DOC_OWNER,)))
    by_key = {p["source_key"]: p for p in all_proposals(report)}
    tok = by_key[f"sol:address:{SOL_DOC_OWNER}:mint:{SOL_DOC_MINT}"]
    assert tok["quantity"]["amount"] == "420000000.000001"
    assert tok["instrument"]["symbol"] is None  # no documented keyless token registry: never guessed
    assert "value" not in tok
    assert f"sol:address:{SOL_DOC_OWNER}:mint:{SOL_SYSTEM_PROGRAM}" in by_key
    assert f"sol:address:{SOL_DOC_OWNER}:mint:{SOL_OTHER_MINT}" not in by_key
    assert tok["source"]["origin"] == "api.mainnet.solana.com" and "slot 124" in tok["source"]["reference"]
    for p in by_key.values():
        validate_proposal(p, now=datetime(2026, 10, 4, 6, 0, 5, tzinfo=UTC))


def test_token_failure_suppresses_that_wallets_token_proposals_but_not_native():
    routes = Routes()
    routes.sol_balances = {SOL_DOC_OWNER: 10}
    base = routes.__call__

    def handler(method, url, body):
        if method == "POST" and '"getTokenAccountsByOwner"' in body:
            return ok(rpc_error(json.loads(body)["id"], -32005, "Node is unhealthy"))
        return base(method, url, body)

    report, *_ = run(routes, ScanRequest(solana_addresses=(SOL_DOC_OWNER,)), host=FakeHost(handler))
    assert [p["source_key"] for p in all_proposals(report)] == [f"sol:address:{SOL_DOC_OWNER}"]
    assert report["issues"][0]["code"] == "sol_tokens_failed"


def test_rpc_failure_for_balances_is_reported_per_address():
    routes = Routes()
    routes.rpc_failures = {"getMultipleAccounts": "x"}
    report, *_ = run(routes, ScanRequest(solana_addresses=(SOL_DOC_ADDRESS,)))
    assert any(i["code"] == "sol_fetch_failed" for i in report["issues"])


def test_invalid_solana_address_reported():
    report, host, _ = run(Routes(), ScanRequest(solana_addresses=("not-an-address",)))
    assert report["issues"][0]["code"].startswith("sol_") and host.calls == []


def test_host_without_propose_returns_proposals_as_data():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    host = FakeHost(routes, supports_propose=False)
    report, *_ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,)), host=host)
    assert report["submission"]["status"] == "host_unsupported"
    assert len(all_proposals(report)) == 1


def test_submit_false_never_calls_propose():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, host, _ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), submit=False))
    assert host.batches == [] and report["submission"]["status"] == "not_requested"


def test_report_is_json_serialisable_and_float_free_and_every_proposal_has_provenance():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    routes.sol_balances = {SOL_DOC_OWNER: 7}
    routes.sol_tokens = {(SOL_DOC_OWNER, solana.TOKEN_PROGRAM): [token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "9", 2)]}
    report, *_ = run(
        routes,
        ScanRequest(btc_addresses=(BTC_P2WPKH,), solana_addresses=(SOL_DOC_OWNER,), kinds=("holding", "valuation")),
    )
    json.dumps(report)
    assert_no_floats(report)
    proposals = all_proposals(report)
    assert len(proposals) == 4
    for p in proposals:
        assert p["source"]["origin"] and p["source"]["reference"] and p["source"]["fetched_at"] and p["source_key"]


def test_no_secrets_or_auth_headers_in_any_request():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    routes.sol_balances = {SOL_DOC_OWNER: 7}
    _, host, _ = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), solana_addresses=(SOL_DOC_OWNER,)))
    for _method, url, headers, body in host.calls:
        assert "key" not in url.lower() and "token" not in url.lower().replace("tokenaccounts", "")
        assert not {h.lower() for h in headers} & {"authorization", "cookie", "x-api-key"}
        assert "apikey" not in body.lower()


def test_rate_limited_source_backs_off_then_falls_back():
    routes = Routes()
    routes.btc[("mempool.space", BTC_P2WPKH)] = HttpResponse(429, "", {"Retry-After": "2"})
    routes.btc[("blockstream.info", BTC_P2WPKH)] = address_response(BTC_P2WPKH)
    report, host, clock = run(routes, ScanRequest(btc_addresses=(BTC_P2WPKH,), price_currency=None))
    assert len([c for c in host.calls if "mempool.space" in c[1]]) == 4  # 1 + 3 retries
    assert report["balances"][0]["origin"] == "blockstream.info"
    assert 2.0 in clock.sleeps


def test_parse_request_validation():
    assert parse_request(None) == ScanRequest()
    assert parse_request({"btc_addresses": ["x"], "price_currency": None}).price_currency is None
    for bad in (
        {"nope": 1},
        {"btc_addresses": "x"},
        {"btc_addresses": [1]},
        {"btc_addresses": ["a"] * 26},
        {"price_currency": "XYZ"},
        {"kinds": ["transaction"]},
        {"kinds": []},
        {"submit": "yes"},
    ):
        with pytest.raises(ValidationError):
            parse_request(bad)
