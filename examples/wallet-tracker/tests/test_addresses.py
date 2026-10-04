import pytest

from fixtures import (
    BTC_P2PKH,
    BTC_P2SH,
    BTC_P2TR,
    BTC_P2WPKH,
    BTC_P2WSH,
    SOL_DOC_ADDRESS,
    SOL_DOC_MINT,
    SOL_SYSTEM_PROGRAM,
    SYN_P2PKH,
    SYN_P2SH,
    SYN_P2TR,
    SYN_TESTNET_P2PKH,
)
from wallet_tracker.addresses import (
    base58_decode,
    base58_encode,
    parse_btc_address,
    parse_solana_address,
    segwit_address,
)
from wallet_tracker.errors import ValidationError


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        (BTC_P2PKH, "p2pkh"),
        (BTC_P2SH, "p2sh"),
        (BTC_P2WPKH, "p2wpkh"),
        (BTC_P2WSH, "p2wsh"),
        (BTC_P2TR, "p2tr"),
        (SYN_P2PKH, "p2pkh"),
        (SYN_P2SH, "p2sh"),
        (SYN_P2TR, "p2tr"),
    ],
)
def test_valid_btc_addresses(address, kind):
    assert parse_btc_address(address).kind == kind


def test_bech32_is_normalised_to_lowercase_and_whitespace_trimmed():
    assert parse_btc_address("  " + BTC_P2WPKH.upper() + "\n").normalized == BTC_P2WPKH


def test_base58_case_is_preserved():
    assert parse_btc_address(BTC_P2PKH).normalized == BTC_P2PKH


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", "bad_checksum"),  # BIP-173 invalid: bad checksum
        ("Bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "mixed_case"),
        (
            "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
            "bad_checksum",
        ),  # BIP-350: bech32, not bech32m
        ("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "wrong_network"),
        ("bc1", "bad_length"),
        ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3tb", "bad_charset"),  # 'b' not in the bech32 set
        ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3", "bad_checksum"),
        ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN0", "bad_charset"),  # '0' not in base58
        ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2" * 4, "bad_length"),
        ("1111", "bad_length"),
        ("бc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "bad_charset"),
        ("x" * 100, "bad_length"),
        ("0" * 30, "bad_charset"),
    ],
)
def test_invalid_btc_addresses(address, code):
    with pytest.raises(ValidationError) as err:
        parse_btc_address(address)
    assert err.value.code == code


def test_testnet_base58_is_rejected_as_wrong_network():
    with pytest.raises(ValidationError) as err:
        parse_btc_address(SYN_TESTNET_P2PKH)
    assert err.value.code == "wrong_network"


def test_unsupported_witness_versions_and_bad_programs():
    for witver, program, code in [
        (2, b"\x44" * 32, "unsupported_version"),
        (1, b"\x44" * 31, "bad_length"),
        (0, b"\x44" * 25, "bad_length"),
    ]:
        with pytest.raises(ValidationError) as err:
            parse_btc_address(segwit_address("bc", witver, program))
        assert err.value.code == code


def test_non_string_is_rejected():
    with pytest.raises(ValidationError) as err:
        parse_btc_address(1234)  # type: ignore[arg-type]
    assert err.value.code == "bad_type"


def test_base58_roundtrip_with_leading_zero_bytes():
    data = b"\x00\x00\x01\x02\xff"
    assert base58_decode(base58_encode(data)) == data


@pytest.mark.parametrize("address", [SOL_DOC_ADDRESS, SOL_DOC_MINT, SOL_SYSTEM_PROGRAM])
def test_valid_solana_addresses(address):
    assert parse_solana_address(address) == address


@pytest.mark.parametrize(
    ("address", "code"),
    [
        ("", "empty"),
        ("short", "bad_length"),
        (SOL_DOC_ADDRESS + "1", "bad_length"),  # decodes to 33 bytes
        ("0" * 40, "bad_charset"),
        ("O" * 40, "bad_charset"),
        ("1" * 31, "bad_length"),
        ("z" * 44, "bad_length"),  # 44 chars but decodes beyond 32 bytes
    ],
)
def test_invalid_solana_addresses(address, code):
    with pytest.raises(ValidationError) as err:
        parse_solana_address(address)
    assert err.value.code == code
