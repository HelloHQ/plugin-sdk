import json

import pytest

from fixtures import BTC_P2WPKH, PRICES, TXID, address_response, utxo_response
from wallet_tracker import btc
from wallet_tracker.errors import ParseError


def test_address_valid():
    bal = btc.parse_address_response(address_response(BTC_P2WPKH, 150_000_000, 98_770_000, 10, 4000), BTC_P2WPKH)
    assert bal.confirmed_sats == 51_230_000
    assert bal.unconfirmed_delta_sats == -3990
    assert bal.tx_count == 4


def test_address_empty_wallet_is_zero_not_error():
    assert btc.parse_address_response(address_response(BTC_P2WPKH, 0, 0), BTC_P2WPKH).confirmed_sats == 0


def test_address_unknown_fields_ignored():
    obj = json.loads(address_response(BTC_P2WPKH))
    obj["something_new"] = {"x": 1}
    obj["chain_stats"]["extra"] = 7
    assert btc.parse_address_response(json.dumps(obj), BTC_P2WPKH).confirmed_sats == 51_230_000


def test_address_case_insensitive_echo():
    assert btc.parse_address_response(address_response(BTC_P2WPKH.upper()), BTC_P2WPKH).confirmed_sats > 0


def test_address_huge_values_exact():
    sats = 2_100_000_000_000_000
    assert btc.parse_address_response(address_response(BTC_P2WPKH, sats, 0), BTC_P2WPKH).confirmed_sats == sats


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.pop("address"),
        lambda o: o.update(address="bc1qother"),
        lambda o: o.pop("chain_stats"),
        lambda o: o.pop("mempool_stats"),
        lambda o: o["chain_stats"].pop("funded_txo_sum"),  # e.g. Elements chains lack the sums
        lambda o: o["chain_stats"].update(spent_txo_sum="5"),
        lambda o: o["chain_stats"].update(funded_txo_sum=1.5),
        lambda o: o["chain_stats"].update(funded_txo_sum=True),
        lambda o: o["chain_stats"].update(funded_txo_sum=-1),
        lambda o: o["chain_stats"].update(spent_txo_sum=10**12),  # spent > funded
        lambda o: o["chain_stats"].pop("tx_count"),
    ],
)
def test_address_invalid_shapes_raise(mutate):
    obj = json.loads(address_response(BTC_P2WPKH))
    mutate(obj)
    with pytest.raises(ParseError):
        btc.parse_address_response(json.dumps(obj), BTC_P2WPKH)


@pytest.mark.parametrize("text", ["", "null", "[]", '"x"', "{", "42"])
def test_address_garbage_raises(text):
    with pytest.raises(ParseError):
        btc.parse_address_response(text, BTC_P2WPKH)


def test_utxo_valid_and_empty():
    utxos = btc.parse_utxo_response(utxo_response(3))
    assert [u.value_sats for u in utxos] == [1000, 1001, 1002]
    assert all(u.confirmed for u in utxos)
    assert btc.parse_utxo_response("[]") == []


def test_utxo_unconfirmed_status():
    body = json.dumps([{"txid": TXID, "vout": 0, "value": 5, "status": {"confirmed": False}}])
    assert btc.parse_utxo_response(body)[0].confirmed is False


def test_utxo_huge_input_is_capped():
    body = utxo_response(50)
    with pytest.raises(ParseError) as err:
        btc.parse_utxo_response(body, max_items=10)
    assert err.value.code == "too_many_utxos"


@pytest.mark.parametrize(
    "item",
    [
        {"vout": 0, "value": 1, "status": {"confirmed": True}},
        {"txid": "zz", "vout": 0, "value": 1, "status": {"confirmed": True}},
        {"txid": TXID, "vout": 0, "value": 1},
        {"txid": TXID, "vout": 0, "value": 1, "status": {"confirmed": "yes"}},
        {"txid": TXID, "vout": "0", "value": 1, "status": {"confirmed": True}},
        {"txid": TXID, "vout": 0, "value": 1.5, "status": {"confirmed": True}},
        "nope",
    ],
)
def test_utxo_invalid_items_raise(item):
    with pytest.raises(ParseError):
        btc.parse_utxo_response(json.dumps([item]))


def test_utxo_not_an_array():
    with pytest.raises(ParseError):
        btc.parse_utxo_response("{}")


def test_prices_valid_unknown_keys_ignored():
    prices = btc.parse_prices_response(PRICES)
    assert prices.time == 1791124205
    assert str(prices.prices["USD"]) == "60000.55"
    assert prices.prices["JPY"] == 9000000
    extra = btc.parse_prices_response('{"time":1,"USD":2,"note":"x","exchangeRates":{"a":1}}')
    assert set(extra.prices) == {"USD"}


@pytest.mark.parametrize(
    "text",
    [
        '{"USD":1}',
        '{"time":1}',
        '{"time":1,"USD":"5"}',
        '{"time":1,"USD":0}',
        '{"time":1,"USD":-2}',
        '{"time":1,"USD":null}',
        '{"time":"1","USD":2}',
        "[]",
        "",
        '{"time":1,"USD":1e999}',
    ],
)
def test_prices_invalid_raise(text):
    with pytest.raises(ParseError):
        btc.parse_prices_response(text)


def test_url_builders_only_allow_known_sources():
    assert btc.url_for("mempool.space", btc.address_path("x")) == "https://mempool.space/api/address/x"
    assert btc.url_for("blockstream.info", btc.PRICES_PATH).startswith("https://blockstream.info/")
    with pytest.raises(ValueError):
        btc.url_for("evil.example", "/")
