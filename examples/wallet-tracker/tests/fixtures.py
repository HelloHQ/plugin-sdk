"""Hand-written response fixtures.

Shapes come from the vendors' DOCUMENTATION, not from captured traffic:

* mempool.space  https://mempool.space/docs/api/rest   (address, utxo)
* Esplora        https://github.com/Blockstream/esplora/blob/master/API.md
* Solana RPC     https://solana.com/docs/rpc/http/getmultipleaccounts
                 https://solana.com/docs/rpc/http/getbalance
                 https://solana.com/docs/rpc/http/gettokenaccountsbyowner
* JSON-RPC 2.0   https://www.jsonrpc.org/specification

EXCEPTION: ``PRICES`` - the mempool.space docs name ``GET /api/v1/prices`` but
show no response example. That fixture mirrors the shape observed once during
development and is marked UNVERIFIED in the README.

Addresses are the public examples from BIP-173 / BIP-350 / the Bitcoin wiki /
the Solana docs, or clearly synthetic (built from fixed byte patterns). None
belongs to a real person.
"""

from __future__ import annotations

import json

from wallet_tracker.addresses import base58check_encode, segwit_address

# Documented public example addresses.
BTC_P2WPKH = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"  # BIP-173
BTC_P2WSH = "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3"  # BIP-173
BTC_P2TR = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"  # BIP-350
BTC_P2PKH = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"  # Bitcoin wiki example
BTC_P2SH = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"  # Bitcoin wiki example
# Synthetic (fixed byte patterns).
SYN_P2PKH = base58check_encode(b"\x00" + b"\x11" * 20)
SYN_P2SH = base58check_encode(b"\x05" + b"\x22" * 20)
SYN_P2TR = segwit_address("bc", 1, b"\x33" * 32)
SYN_TESTNET_P2PKH = base58check_encode(b"\x6f" + b"\x11" * 20)

SOL_DOC_ADDRESS = "83astBRguLMdt2h5U1Tpdq5tjFoJ6noeGwaY3mDLVcri"  # Solana docs getBalance
SOL_SYSTEM_PROGRAM = "11111111111111111111111111111111"
SOL_DOC_OWNER = "A1TMhSGzQxMr1TboBKtgixKz1sS6REASMxPo1qsyTSJd"  # Solana docs getTokenAccountsByOwner
SOL_DOC_MINT = "2cHr7QS3xfuSV8wdxo3ztuF4xbiarF6Nrgx3qpx3HzXR"  # Solana docs getTokenAccountsByOwner
SOL_DOC_TOKEN_ACCOUNT = "BGocb4GEpbTFm8UFV2VsDSaBXHELPfAXrvd4vtt8QWrA"  # Solana docs
SOL_OTHER_MINT = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # reused only as a syntactically valid pubkey
TXID = "ab" * 32


def stats(funded: int, spent: int, tx_count: int) -> dict:
    return {
        "tx_count": tx_count,
        "funded_txo_count": 1,
        "funded_txo_sum": funded,
        "spent_txo_count": 1,
        "spent_txo_sum": spent,
    }


def address_response(address: str, funded=150_000_000, spent=98_770_000, m_funded=0, m_spent=0) -> str:
    return json.dumps(
        {"address": address, "chain_stats": stats(funded, spent, 4), "mempool_stats": stats(m_funded, m_spent, 0)}
    )


def utxo_response(n: int = 2) -> str:
    return json.dumps(
        [
            {
                "txid": f"{i:02x}" * 32,
                "vout": i,
                "value": 1000 + i,
                "status": {
                    "confirmed": True,
                    "block_height": 800000 + i,
                    "block_hash": "00" * 32,
                    "block_time": 1700000000,
                },
            }
            for i in range(n)
        ]
    )


# UNVERIFIED shape (see module docstring). Hand-written text keeps number formatting exact.
PRICES = '{"time":1791124205,"USD":60000.55,"EUR":55000,"GBP":47000,"CAD":82000,"CHF":53000,"AUD":90000,"JPY":9000000}'


def rpc_ok(request_id: int, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "result": result, "id": request_id})


def rpc_error(request_id: int, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": request_id})


def multiple_accounts_result(lamports: list[int | None], slot: int = 123) -> dict:
    value = [
        None
        if lam is None
        else {
            "data": ["", "base64"],
            "executable": False,
            "lamports": lam,
            "owner": SOL_SYSTEM_PROGRAM,
            "rentEpoch": 18446744073709551615,
            "space": 0,
        }
        for lam in lamports
    ]
    return {"context": {"apiVersion": "3.1.8", "slot": slot}, "value": value}


def token_account(
    mint: str, owner: str, amount: str, decimals: int, pubkey: str = SOL_DOC_TOKEN_ACCOUNT, program: str = "spl-token"
) -> dict:
    return {
        "pubkey": pubkey,
        "account": {
            "data": {
                "program": program,
                "parsed": {
                    "type": "account",
                    "info": {
                        "mint": mint,
                        "owner": owner,
                        "state": "initialized",
                        "tokenAmount": {
                            "amount": amount,
                            "decimals": decimals,
                            "uiAmount": 1.5,
                            "uiAmountString": "1.5",
                        },
                    },
                },
                "space": 165,
            }
        },
    }


def token_accounts_result(accounts: list[dict], slot: int = 124) -> dict:
    return {"context": {"apiVersion": "3.1.8", "slot": slot}, "value": accounts}
