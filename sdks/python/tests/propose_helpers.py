"""Shared fixtures for the propose tests: a valid proposal batch and a fake stdio host."""

from __future__ import annotations

import io
import itertools
import json
import sys
from datetime import UTC, datetime, timedelta

import hellohq_plugin_sdk.host as host

NOW = datetime(2026, 10, 4, 6, 0, 30, tzinfo=UTC)
RUN_START = NOW - timedelta(seconds=60)
FETCHED = NOW - timedelta(seconds=28)


def holding_wire(**over):
    out = {
        "kind": "holding",
        "source_key": "btc:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
        "asset_kind": "crypto_ticker",
        "display_name": "Cold wallet (bc1qxy...0wlh)",
        "instrument": {"symbol": "BTC", "chain": "bitcoin"},
        "quantity": {"amount": "0.5123", "unit": "BTC"},
        "value": {"amount": "31744.12", "currency": "USD"},
        "as_of": "2026-10-04T06:00:00Z",
        "method": "quoted_price",
        "source": {
            "origin": "mempool.space",
            "reference": "/api/address/bc1qxy.../utxo",
            "fetched_at": "2026-10-04T06:00:02Z",
            "price_origin": "api.coingecko.com",
        },
    }
    out.update(over)
    return out


def valuation_wire(**over):
    out = {
        "kind": "valuation",
        "source_key": "uk-lr:uprn:100023336956",
        "value": {"amount": "412500", "currency": "GBP"},
        "as_of": "2026-09-30T00:00:00Z",
        "method": "comparable_sales_median",
        "source": {
            "origin": "landregistry.data.gov.uk",
            "reference": "price-paid: 14 sales within 400 m, last 12 months",
            "fetched_at": "2026-10-04T06:00:10Z",
        },
    }
    out.update(over)
    return out


def batch_wire(*proposals):
    return {"schema": "hellohq.proposal-batch@1", "proposals": list(proposals)}


def exchange(fn, replies, monkeypatch):
    """Run host call *fn* with canned stdin *replies* (dicts, one per line).

    Returns ``(result, sent)``: ``sent`` is every NDJSON line the SDK wrote.
    The seq counter starts at 0 so a canned reply can name its seq.
    """
    monkeypatch.setattr(host, "_seq_counter", itertools.count())
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in replies))
    )
    result = fn()
    return result, [json.loads(line) for line in out.getvalue().splitlines()]
