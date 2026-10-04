"""Bitcoin: request builders and response parsers for mempool.space / Esplora.

Both services speak the Esplora REST shape for the endpoints used here.

Documented shapes (the only ones relied upon):

* ``GET /api/address/:address`` -> ``{address, chain_stats{tx_count,
  funded_txo_count, funded_txo_sum, spent_txo_count, spent_txo_sum},
  mempool_stats{same}}``
  - https://mempool.space/docs/api/rest (Get Address)
  - https://github.com/Blockstream/esplora/blob/master/API.md
* ``GET /api/address/:address/utxo`` -> ``[{txid, vout, value,
  status{confirmed, block_height, block_hash, block_time}}]``
* ``GET /api/v1/prices`` - the endpoint is documented ("returns bitcoin's
  latest price denominated in main currencies") but the docs page has NO
  response example. The parser therefore accepts the shape observed when this
  plugin was written ``{time, USD, EUR, GBP, CAD, CHF, AUD, JPY}``,
  ignores unknown keys, and raises ``ParseError`` for anything missing. See
  the README ("Unverified response shapes").

Public Blockstream Esplora serves the same paths under ``/api/``.

Balance semantics: confirmed balance = ``funded_txo_sum - spent_txo_sum`` of
``chain_stats`` (satoshis). Unconfirmed (mempool) delta is reported separately
and is never proposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from wallet_tracker.errors import ParseError
from wallet_tracker.money import loads_decimal, parse_price, require_int

MEMPOOL_HOST = "mempool.space"
ESPLORA_HOST = "blockstream.info"
SOURCES = (MEMPOOL_HOST, ESPLORA_HOST)  # preference order
MAX_UTXOS = 10_000

_TXID = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def address_path(address: str) -> str:
    return f"/api/address/{address}"


def utxo_path(address: str) -> str:
    return f"/api/address/{address}/utxo"


PRICES_PATH = "/api/v1/prices"


def url_for(host: str, path: str) -> str:
    if host not in SOURCES:
        raise ValueError(f"unknown BTC source {host!r}")
    return f"https://{host}{path}"


@dataclass(frozen=True)
class BtcBalance:
    address: str
    confirmed_sats: int
    unconfirmed_delta_sats: int  # may be negative (pending spend)
    tx_count: int


def _stats(obj: Any, name: str) -> tuple[int, int, int]:
    if not isinstance(obj, dict):
        raise ParseError(f"{name}: expected an object")
    funded = require_int(obj.get("funded_txo_sum"), f"{name}.funded_txo_sum")
    spent = require_int(obj.get("spent_txo_sum"), f"{name}.spent_txo_sum")
    tx_count = require_int(obj.get("tx_count"), f"{name}.tx_count")
    return funded, spent, tx_count


def parse_address_response(text: str, expected_address: str) -> BtcBalance:
    data = loads_decimal(text)
    if not isinstance(data, dict):
        raise ParseError("address response: expected an object")
    echoed = data.get("address")
    if not isinstance(echoed, str):
        raise ParseError("address response: missing 'address'")
    if echoed.lower() != expected_address.lower():
        raise ParseError("address response is for a different address")
    funded, spent, tx_count = _stats(data.get("chain_stats"), "chain_stats")
    if spent > funded:
        raise ParseError("chain_stats: spent exceeds funded")
    m_funded, m_spent, _ = _stats(data.get("mempool_stats"), "mempool_stats")
    return BtcBalance(
        address=expected_address,
        confirmed_sats=funded - spent,
        unconfirmed_delta_sats=m_funded - m_spent,
        tx_count=tx_count,
    )


@dataclass(frozen=True)
class Utxo:
    txid: str
    vout: int
    value_sats: int
    confirmed: bool


def parse_utxo_response(text: str, *, max_items: int = MAX_UTXOS) -> list[Utxo]:
    data = loads_decimal(text)
    if not isinstance(data, list):
        raise ParseError("utxo response: expected an array")
    if len(data) > max_items:
        raise ParseError(f"utxo response: more than {max_items} entries", code="too_many_utxos")
    out: list[Utxo] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ParseError(f"utxo[{i}]: expected an object")
        txid = item.get("txid")
        if not isinstance(txid, str) or not _TXID.match(txid):
            raise ParseError(f"utxo[{i}].txid: expected 64 hex characters")
        status = item.get("status")
        if not isinstance(status, dict) or not isinstance(status.get("confirmed"), bool):
            raise ParseError(f"utxo[{i}].status.confirmed: expected a boolean")
        out.append(
            Utxo(
                txid=txid,
                vout=require_int(item.get("vout"), f"utxo[{i}].vout"),
                value_sats=require_int(item.get("value"), f"utxo[{i}].value"),
                confirmed=status["confirmed"],
            )
        )
    return out


@dataclass(frozen=True)
class BtcPrices:
    time: int  # unix seconds, as reported by the service
    prices: dict[str, Decimal]  # currency -> price of 1 BTC


def parse_prices_response(text: str) -> BtcPrices:
    data = loads_decimal(text)
    if not isinstance(data, dict):
        raise ParseError("prices response: expected an object")
    stamp = require_int(data.get("time"), "prices.time", minimum=1)
    prices: dict[str, Decimal] = {}
    for key, value in data.items():
        if key == "time" or not _CURRENCY.match(key):
            continue  # unknown fields are ignored
        prices[key] = parse_price(value, f"prices.{key}")
    if not prices:
        raise ParseError("prices response: no currency prices")
    return BtcPrices(time=stamp, prices=prices)
