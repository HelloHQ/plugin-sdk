"""Bitcoin and Solana wallet tracker - Tier-1 Python sidecar entry point.

Thin adapter only: all logic lives in the ``wallet_tracker`` package (pure,
unit-tested, no SDK imports). ``build.sh`` bundles the package and this file
into the single-file sidecar artifact ``dist/plugin.py``.

Functions (host calls ``run`` with
``args = {"context": {...}, "input": {"function": <fn>, "args": {...}}}``):

* ``scan`` (default) - ``args``: ``btc_addresses``, ``solana_addresses``
  (public addresses the person entered), ``price_currency`` (default "USD" or
  null), ``kinds`` (["holding"] or add "valuation"), ``include_utxo_count``,
  ``submit``. Returns a report with balances, proposal batches, submission
  status and issues.

Permissions: network:fetch (mempool.space, blockstream.info,
api.mainnet.solana.com), propose:holdings, propose:valuations - the two
``propose:*`` ids are PENDING HOST SUPPORT (see README).
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve
from hellohq_plugin_sdk.protocol import ERR_INVALID_INPUT

from wallet_tracker.errors import ValidationError
from wallet_tracker.sdk_host import SdkHost
from wallet_tracker.tracker import parse_request, scan


def dispatch(function: str, args: Any):
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "scan")
    if fn not in ("scan", "run"):
        raise UnsupportedFunction(str(fn))
    try:
        request = parse_request(inner.get("args"))
    except ValidationError as exc:
        raise PluginError(exc.message, ERR_INVALID_INPUT) from exc
    return scan(SdkHost(), request)


if __name__ == "__main__":
    serve(dispatch)
