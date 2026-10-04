"""Solana: JSON-RPC request builders and response parsers.

Documented shapes relied upon (all https://solana.com/docs/rpc/http/...):

* ``getMultipleAccounts`` - up to 100 pubkeys; ``result.context.slot``;
  ``result.value[]`` in request order, each an account object with
  ``lamports`` or ``null`` when the account does not exist.
* ``getBalance`` - ``result.value`` lamports (parser provided; the tracker
  uses ``getMultipleAccounts`` to batch).
* ``getTokenAccountsByOwner`` with ``{"programId": ...}`` and
  ``{"encoding": "jsonParsed"}`` - ``result.value[]`` of ``{pubkey, account{
  data{program, parsed{type, info{mint, owner, state, tokenAmount{amount,
  decimals, uiAmount, uiAmountString}}}}}}``. ``uiAmount`` is a float and is
  IGNORED; only the integer-string ``amount`` and ``decimals`` are used.
* JSON-RPC 2.0 error object ``{code, message}`` (https://www.jsonrpc.org/specification).

Not documented in what was reviewed: the jsonParsed shape for the Token-2022
program (``spl-token-2022``). The parser accepts it only if it matches the
same fields and fails explicitly otherwise.

Public RPC limits (https://solana.com/docs/references/clusters): 100 requests
/ 10 s / IP overall, 40 / 10 s for a single method, and "not intended for
production applications".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from wallet_tracker.errors import CoreError, ParseError, ValidationError
from wallet_tracker.money import loads_decimal, require_int, require_uint_string

RPC_HOST = "api.mainnet.solana.com"
RPC_URL = f"https://{RPC_HOST}"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 - public program id, not a secret
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105 - public program id, not a secret
TOKEN_PROGRAMS = (TOKEN_PROGRAM, TOKEN_2022_PROGRAM)
MAX_ACCOUNTS_PER_CALL = 100
LAMPORT_DECIMALS = 9
_PARSED_PROGRAMS = {"spl-token", "spl-token-2022"}
_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class RpcError(CoreError):
    code = "rpc_error"

    def __init__(self, message: str, rpc_code: int | None = None) -> None:
        super().__init__(message)
        self.rpc_code = rpc_code


def _request(method: str, params: list[Any], request_id: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, separators=(",", ":"))


def build_get_multiple_accounts(addresses: list[str], request_id: int) -> str:
    if not 1 <= len(addresses) <= MAX_ACCOUNTS_PER_CALL:
        raise ValidationError(f"getMultipleAccounts takes 1..{MAX_ACCOUNTS_PER_CALL} addresses")
    # dataSlice length 0: we only need lamports, not account data.
    config = {"commitment": "finalized", "encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}
    return _request("getMultipleAccounts", [addresses, config], request_id)


def build_get_balance(address: str, request_id: int) -> str:
    return _request("getBalance", [address, {"commitment": "finalized"}], request_id)


def build_get_token_accounts_by_owner(owner: str, program_id: str, request_id: int) -> str:
    if program_id not in TOKEN_PROGRAMS:
        raise ValidationError("unknown token program")
    return _request(
        "getTokenAccountsByOwner",
        [owner, {"programId": program_id}, {"encoding": "jsonParsed", "commitment": "finalized"}],
        request_id,
    )


def parse_envelope(text: str, expected_id: int) -> dict[str, Any]:
    """Return the ``result`` object; raise ``RpcError`` for a JSON-RPC error."""
    data = loads_decimal(text)
    if not isinstance(data, dict):
        raise ParseError("rpc response: expected an object")
    if data.get("id") != expected_id:
        raise ParseError("rpc response: id does not match the request")
    error = data.get("error")
    if error is not None:
        if not isinstance(error, dict):
            raise ParseError("rpc error: expected an object")
        message = error.get("message")
        code = error.get("code")
        raise RpcError(
            message if isinstance(message, str) else "rpc error",
            code if isinstance(code, int) and not isinstance(code, bool) else None,
        )
    result = data.get("result")
    if not isinstance(result, dict):
        raise ParseError("rpc response: missing 'result' object")
    return result


def _slot(result: dict[str, Any]) -> int:
    context = result.get("context")
    if not isinstance(context, dict):
        raise ParseError("result.context: expected an object")
    return require_int(context.get("slot"), "result.context.slot")


@dataclass(frozen=True)
class SolBalance:
    address: str
    lamports: int
    account_exists: bool
    slot: int


def parse_get_balance(result: dict[str, Any], address: str) -> SolBalance:
    return SolBalance(address, require_int(result.get("value"), "result.value"), True, _slot(result))


def parse_multiple_accounts(result: dict[str, Any], addresses: list[str]) -> list[SolBalance]:
    slot = _slot(result)
    values = result.get("value")
    if not isinstance(values, list):
        raise ParseError("result.value: expected an array")
    if len(values) != len(addresses):
        raise ParseError("result.value: length differs from the request")
    out: list[SolBalance] = []
    for address, item in zip(addresses, values, strict=True):
        if item is None:  # documented: null = account does not exist
            out.append(SolBalance(address, 0, False, slot))
            continue
        if not isinstance(item, dict):
            raise ParseError(f"account {address}: expected an object or null")
        out.append(SolBalance(address, require_int(item.get("lamports"), "lamports"), True, slot))
    return out


@dataclass(frozen=True)
class TokenAccount:
    pubkey: str
    mint: str
    amount: int  # raw units
    decimals: int
    program: str


def parse_token_accounts(result: dict[str, Any], owner: str) -> tuple[list[TokenAccount], int]:
    """Returns (token accounts, slot). Strict: any malformed entry raises."""
    slot = _slot(result)
    values = result.get("value")
    if not isinstance(values, list):
        raise ParseError("result.value: expected an array")
    out: list[TokenAccount] = []
    for i, item in enumerate(values):
        where = f"value[{i}]"
        if not isinstance(item, dict):
            raise ParseError(f"{where}: expected an object")
        pubkey = item.get("pubkey")
        account = item.get("account")
        if not isinstance(pubkey, str) or not isinstance(account, dict):
            raise ParseError(f"{where}: missing pubkey/account")
        data = account.get("data")
        if not isinstance(data, dict):
            raise ParseError(f"{where}.account.data: expected parsed (jsonParsed) data")
        program = data.get("program")
        if program not in _PARSED_PROGRAMS:
            raise ParseError(f"{where}: unexpected program {program!r}")
        parsed = data.get("parsed")
        info = parsed.get("info") if isinstance(parsed, dict) else None
        if not isinstance(info, dict):
            raise ParseError(f"{where}: missing parsed.info")
        mint = info.get("mint")
        if not isinstance(mint, str) or not _BASE58.match(mint):
            raise ParseError(f"{where}: invalid mint")
        if info.get("owner") != owner:
            raise ParseError(f"{where}: token account belongs to a different owner")
        if not isinstance(info.get("state"), str):
            raise ParseError(f"{where}: missing state")
        token_amount = info.get("tokenAmount")
        if not isinstance(token_amount, dict):
            raise ParseError(f"{where}: missing tokenAmount")
        decimals = require_int(token_amount.get("decimals"), f"{where}.decimals")
        if decimals > 255:
            raise ParseError(f"{where}: decimals out of range")
        out.append(
            TokenAccount(
                pubkey=pubkey,
                mint=mint,
                amount=require_uint_string(token_amount.get("amount"), f"{where}.amount"),
                decimals=decimals,
                program=program,
            )
        )
    return out, slot


@dataclass(frozen=True)
class TokenHolding:
    mint: str
    amount: int
    decimals: int
    accounts: int


def aggregate_by_mint(accounts: list[TokenAccount]) -> list[TokenHolding]:
    """Sum an owner's token accounts per mint; zero totals are dropped.

    Different ``decimals`` for one mint is impossible on-chain, so it is
    treated as a parse error rather than reconciled.
    """
    totals: dict[str, list[int]] = {}
    decimals: dict[str, int] = {}
    for acct in accounts:
        if decimals.setdefault(acct.mint, acct.decimals) != acct.decimals:
            raise ParseError(f"mint {acct.mint}: inconsistent decimals")
        entry = totals.setdefault(acct.mint, [0, 0])
        entry[0] += acct.amount
        entry[1] += 1
    return [TokenHolding(m, t[0], decimals[m], t[1]) for m, t in sorted(totals.items()) if t[0] > 0]
