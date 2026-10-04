"""Public address validation: Bitcoin (legacy / SegWit / Taproot) and Solana.

Addresses are PUBLIC data the person enters. Validation is purely syntactic
and checksum-based; it never contacts the network.

Bitcoin (mainnet only):
  * P2PKH  - Base58Check, version 0x00, starts with ``1``
  * P2SH   - Base58Check, version 0x05, starts with ``3``
  * P2WPKH / P2WSH - Bech32 (BIP-173), witness v0, 20- / 32-byte program
  * P2TR   - Bech32m (BIP-350), witness v1, 32-byte program
  Witness versions 2..16 are refused (no defined spend rules to validate).
  Testnet/signet/regtest addresses are refused with ``wrong_network``.

Solana: Base58 that decodes to exactly 32 bytes. Whether the key is on the
ed25519 curve is NOT checked (a program-derived address is a valid holder).

References: BIP-173, BIP-350, Bitcoin wiki "Base58Check encoding",
https://solana.com/docs/core/accounts (addresses are 32-byte base58 pubkeys).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from wallet_tracker.errors import ValidationError

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def base58_decode(text: str) -> bytes:
    if not text:
        raise ValidationError("empty address", code="empty")
    number = 0
    for char in text:
        try:
            number = number * 58 + _B58_INDEX[char]
        except KeyError:
            raise ValidationError(f"invalid base58 character {char!r}", code="bad_charset") from None
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + body


def base58_encode(data: bytes) -> str:
    """Used by tests to build clearly synthetic addresses."""
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rem = divmod(number, 58)
        out = _B58[rem] + out
    leading = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading + out


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def base58check_encode(payload: bytes) -> str:
    return base58_encode(payload + _checksum(payload))


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if (top >> i) & 1 else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int] | None:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or value >> frombits:
            return None
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return out


def bech32_encode(hrp: str, data: list[int], constant: int) -> str:
    """Used by tests to build clearly synthetic addresses."""
    polymod = _bech32_polymod(_hrp_expand(hrp) + data + [0] * 6) ^ constant
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in data + checksum)


def segwit_address(hrp: str, witver: int, program: bytes) -> str:
    """Encode a witness program (tests only)."""
    constant = _BECH32_CONST if witver == 0 else _BECH32M_CONST
    five = _convertbits(list(program), 8, 5, True)
    assert five is not None
    return bech32_encode(hrp, [witver] + five, constant)


@dataclass(frozen=True)
class BtcAddress:
    kind: str  # p2pkh | p2sh | p2wpkh | p2wsh | p2tr
    normalized: str  # canonical text: bech32 lower-cased, base58 as entered


def parse_btc_address(text: object) -> BtcAddress:
    if not isinstance(text, str):
        raise ValidationError("address must be text", code="bad_type")
    address = text.strip()
    if not address:
        raise ValidationError("empty address", code="empty")
    if len(address) > 90:
        raise ValidationError("too long to be a Bitcoin address", code="bad_length")
    if not address.isascii():
        raise ValidationError("not a Bitcoin address", code="bad_charset")
    lowered = address.lower()
    if lowered.startswith(("bc1", "tb1", "bcrt1")):
        return _parse_segwit(address)
    return _parse_base58(address)


def _parse_base58(address: str) -> BtcAddress:
    raw = base58_decode(address)
    if len(raw) != 25:
        raise ValidationError("wrong length for a Base58Check address", code="bad_length")
    payload, check = raw[:-4], raw[-4:]
    if _checksum(payload) != check:
        raise ValidationError("checksum mismatch", code="bad_checksum")
    version = payload[0]
    if version == 0x00:
        return BtcAddress("p2pkh", address)
    if version == 0x05:
        return BtcAddress("p2sh", address)
    if version in (0x6F, 0xC4):
        raise ValidationError("testnet address; only Bitcoin mainnet is supported", code="wrong_network")
    raise ValidationError(f"unknown address version 0x{version:02x}", code="unsupported_version")


def _parse_segwit(address: str) -> BtcAddress:
    if address != address.lower() and address != address.upper():
        raise ValidationError("mixed-case bech32", code="mixed_case")
    address = address.lower()
    pos = address.rfind("1")
    if pos < 1 or pos + 7 > len(address):
        raise ValidationError("malformed bech32", code="bad_length")
    hrp, tail = address[:pos], address[pos + 1 :]
    if any(ch not in _BECH32_CHARSET for ch in tail):
        raise ValidationError("invalid bech32 character", code="bad_charset")
    if hrp != "bc":
        raise ValidationError("not a Bitcoin mainnet (bc) address", code="wrong_network")
    values = [_BECH32_CHARSET.index(ch) for ch in tail]
    polymod = _bech32_polymod(_hrp_expand(hrp) + values)
    if polymod not in (_BECH32_CONST, _BECH32M_CONST):
        raise ValidationError("checksum mismatch", code="bad_checksum")
    encoding = polymod
    data = values[:-6]
    if not data:
        raise ValidationError("missing witness version", code="bad_length")
    witver = data[0]
    if witver > 16:
        raise ValidationError("invalid witness version", code="unsupported_version")
    program_bytes = _convertbits(data[1:], 5, 8, False)
    if program_bytes is None or not 2 <= len(program_bytes) <= 40:
        raise ValidationError("invalid witness program", code="bad_length")
    if witver == 0:
        if encoding != _BECH32_CONST:
            raise ValidationError("witness v0 must use bech32, not bech32m", code="bad_checksum")
        if len(program_bytes) == 20:
            return BtcAddress("p2wpkh", address)
        if len(program_bytes) == 32:
            return BtcAddress("p2wsh", address)
        raise ValidationError("witness v0 program must be 20 or 32 bytes", code="bad_length")
    if witver == 1:
        if encoding != _BECH32M_CONST:
            raise ValidationError("witness v1 must use bech32m, not bech32", code="bad_checksum")
        if len(program_bytes) != 32:
            raise ValidationError("taproot program must be 32 bytes", code="bad_length")
        return BtcAddress("p2tr", address)
    raise ValidationError(f"unsupported witness version {witver}", code="unsupported_version")


def parse_solana_address(text: object) -> str:
    if not isinstance(text, str):
        raise ValidationError("address must be text", code="bad_type")
    address = text.strip()
    if not address:
        raise ValidationError("empty address", code="empty")
    if not 32 <= len(address) <= 44:
        raise ValidationError("Solana addresses are 32-44 base58 characters", code="bad_length")
    if len(base58_decode(address)) != 32:
        raise ValidationError("address does not decode to 32 bytes", code="bad_length")
    return address
