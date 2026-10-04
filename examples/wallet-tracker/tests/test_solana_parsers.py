import json

import pytest

from fixtures import (
    SOL_DOC_ADDRESS,
    SOL_DOC_MINT,
    SOL_DOC_OWNER,
    SOL_OTHER_MINT,
    SOL_SYSTEM_PROGRAM,
    multiple_accounts_result,
    rpc_error,
    rpc_ok,
    token_account,
    token_accounts_result,
)
from wallet_tracker import solana
from wallet_tracker.errors import ParseError, ValidationError


def test_request_builders():
    body = json.loads(solana.build_get_multiple_accounts([SOL_DOC_ADDRESS, SOL_SYSTEM_PROGRAM], 7))
    assert body["method"] == "getMultipleAccounts" and body["id"] == 7 and body["jsonrpc"] == "2.0"
    assert body["params"][0] == [SOL_DOC_ADDRESS, SOL_SYSTEM_PROGRAM]
    assert body["params"][1]["dataSlice"] == {"offset": 0, "length": 0}
    tok = json.loads(solana.build_get_token_accounts_by_owner(SOL_DOC_OWNER, solana.TOKEN_PROGRAM, 8))
    assert tok["params"][1] == {"programId": solana.TOKEN_PROGRAM}
    assert tok["params"][2]["encoding"] == "jsonParsed"
    assert json.loads(solana.build_get_balance(SOL_DOC_ADDRESS, 1))["method"] == "getBalance"


def test_multiple_accounts_batch_limit():
    with pytest.raises(ValidationError):
        solana.build_get_multiple_accounts([], 1)
    with pytest.raises(ValidationError):
        solana.build_get_multiple_accounts([SOL_DOC_ADDRESS] * 101, 1)
    solana.build_get_multiple_accounts([SOL_DOC_ADDRESS] * 100, 1)


def test_unknown_token_program_refused():
    with pytest.raises(ValidationError):
        solana.build_get_token_accounts_by_owner(SOL_DOC_OWNER, SOL_DOC_ADDRESS, 1)


def test_envelope_ok_and_errors():
    result = solana.parse_envelope(rpc_ok(3, {"value": 0}), 3)
    assert result == {"value": 0}
    with pytest.raises(solana.RpcError) as err:
        solana.parse_envelope(rpc_error(3, -32602, "Invalid param"), 3)
    assert err.value.rpc_code == -32602 and "Invalid param" in err.value.message
    for bad in (rpc_ok(4, {"value": 0}), "[]", "{}", '{"id":3,"result":5}', '{"id":3,"error":"x"}'):
        with pytest.raises(ParseError):
            solana.parse_envelope(bad, 3)


def test_get_balance_documented_shape():
    result = {"context": {"apiVersion": "3.1.8", "slot": 1}, "value": 0}
    bal = solana.parse_get_balance(result, SOL_DOC_ADDRESS)
    assert (bal.lamports, bal.slot) == (0, 1)


def test_multiple_accounts_null_means_missing_account():
    result = multiple_accounts_result([1_500_000_000, None, 0])
    bals = solana.parse_multiple_accounts(result, [SOL_DOC_ADDRESS, SOL_SYSTEM_PROGRAM, SOL_DOC_OWNER])
    assert [b.lamports for b in bals] == [1_500_000_000, 0, 0]
    assert [b.account_exists for b in bals] == [True, False, True]
    assert bals[0].slot == 123


def test_multiple_accounts_huge_lamports():
    result = multiple_accounts_result([2**64 - 1])
    assert solana.parse_multiple_accounts(result, [SOL_DOC_ADDRESS])[0].lamports == 2**64 - 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("context"),
        lambda r: r["context"].pop("slot"),
        lambda r: r.pop("value"),
        lambda r: r.update(value={}),
        lambda r: r["value"].append(None),  # length mismatch
        lambda r: r["value"][0].pop("lamports"),
        lambda r: r["value"][0].update(lamports="5"),
        lambda r: r["value"][0].update(lamports=1.5),
        lambda r: r["value"].__setitem__(0, 5),
    ],
)
def test_multiple_accounts_invalid_shapes(mutate):
    result = multiple_accounts_result([100])
    mutate(result)
    with pytest.raises(ParseError):
        solana.parse_multiple_accounts(result, [SOL_DOC_ADDRESS])


def test_token_accounts_documented_example():
    accounts = [token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "420000000000000", 6)]
    parsed, slot = solana.parse_token_accounts(token_accounts_result(accounts), SOL_DOC_OWNER)
    assert slot == 124
    assert parsed[0].amount == 420_000_000_000_000 and parsed[0].decimals == 6 and parsed[0].mint == SOL_DOC_MINT


def test_token_accounts_empty_list():
    assert solana.parse_token_accounts(token_accounts_result([]), SOL_DOC_OWNER)[0] == []


def test_token_2022_program_name_accepted_with_same_shape():
    acct = token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "5", 0, program="spl-token-2022")
    assert solana.parse_token_accounts(token_accounts_result([acct]), SOL_DOC_OWNER)[0][0].program == "spl-token-2022"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.pop("pubkey"),
        lambda a: a["account"]["data"].update(program="system"),
        lambda a: a["account"]["data"].update(parsed=None),
        lambda a: a["account"]["data"].__setitem__("parsed", {"info": None}),
        lambda a: a["account"].update(data=["abc", "base64"]),  # not jsonParsed
        lambda a: a["account"]["data"]["parsed"]["info"].update(mint="bad mint"),
        lambda a: a["account"]["data"]["parsed"]["info"].update(owner=SOL_DOC_ADDRESS),  # other owner
        lambda a: a["account"]["data"]["parsed"]["info"].pop("state"),
        lambda a: a["account"]["data"]["parsed"]["info"].pop("tokenAmount"),
        lambda a: a["account"]["data"]["parsed"]["info"]["tokenAmount"].update(amount=5),
        lambda a: a["account"]["data"]["parsed"]["info"]["tokenAmount"].update(amount="-5"),
        lambda a: a["account"]["data"]["parsed"]["info"]["tokenAmount"].update(amount="1.5"),
        lambda a: a["account"]["data"]["parsed"]["info"]["tokenAmount"].pop("decimals"),
        lambda a: a["account"]["data"]["parsed"]["info"]["tokenAmount"].update(decimals=300),
    ],
)
def test_token_accounts_invalid_shapes_raise(mutate):
    acct = token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "5", 6)
    mutate(acct)
    with pytest.raises(ParseError):
        solana.parse_token_accounts(token_accounts_result([acct]), SOL_DOC_OWNER)


def test_ui_amount_float_is_ignored():
    acct = token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "1500000", 6)
    acct["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] = 99999.9  # lying float
    assert solana.parse_token_accounts(token_accounts_result([acct]), SOL_DOC_OWNER)[0][0].amount == 1_500_000


def test_aggregate_sums_per_mint_and_drops_zero():
    accts = [
        token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "100", 6),
        token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "250", 6),
        token_account(SOL_OTHER_MINT, SOL_DOC_OWNER, "0", 0),
    ]
    parsed, _ = solana.parse_token_accounts(token_accounts_result(accts), SOL_DOC_OWNER)
    holdings = solana.aggregate_by_mint(parsed)
    assert [(h.mint, h.amount, h.accounts) for h in holdings] == [(SOL_DOC_MINT, 350, 2)]


def test_aggregate_inconsistent_decimals_is_an_error():
    accts = [token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "1", 6), token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "1", 9)]
    parsed, _ = solana.parse_token_accounts(token_accounts_result(accts), SOL_DOC_OWNER)
    with pytest.raises(ParseError):
        solana.aggregate_by_mint(parsed)


def test_many_token_accounts_huge_input():
    accts = [token_account(SOL_DOC_MINT, SOL_DOC_OWNER, "1", 0) for _ in range(5000)]
    parsed, _ = solana.parse_token_accounts(token_accounts_result(accts), SOL_DOC_OWNER)
    assert solana.aggregate_by_mint(parsed)[0].amount == 5000
