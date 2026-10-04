"""Orchestration: validate input, fetch politely, build and (try to) submit proposals.

Pure with respect to I/O: everything goes through the injected ``Host`` and
``Clock``. Failures of one address never abort the others; every skipped
address or asset is reported in ``issues`` with a stable code (nothing is
silently dropped and nothing is guessed).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wallet_tracker import btc, solana
from wallet_tracker.addresses import parse_btc_address, parse_solana_address
from wallet_tracker.errors import CoreError, HostUnsupported, ValidationError
from wallet_tracker.host import Clock, Host, SystemClock, iso_utc
from wallet_tracker.money import CURRENCY_EXPONENTS, assert_no_floats, fiat_value, format_units
from wallet_tracker.polite import OriginPolicy, PoliteClient
from wallet_tracker.proposals import (
    ASSET_KIND,
    Provenance,
    batches,
    btc_source_key,
    holding,
    short_address,
    sol_source_key,
    validate_proposal,
    valuation,
)

REPORT_SCHEMA = "hellohq.wallet-tracker.report@1"
MAX_ADDRESSES_PER_CHAIN = 25

# Our own conservative pacing. mempool.space and Esplora publish no numbers
# ("rate-limited; abuse can be banned"), so we stay deliberately slow.
# Solana's documented public limits are 100 req/10 s/IP and 40 req/10 s per
# method; we use 20 per 10 s.
DEFAULT_POLICIES: dict[str, OriginPolicy] = {
    btc.MEMPOOL_HOST: OriginPolicy(max_calls=10, window_s=10.0, min_interval_s=1.0),
    btc.ESPLORA_HOST: OriginPolicy(max_calls=10, window_s=10.0, min_interval_s=1.0),
    solana.RPC_HOST: OriginPolicy(max_calls=20, window_s=10.0, min_interval_s=0.5),
}

NO_SOLANA_PRICE_NOTE = (
    "No keyless, documented price source is used for SOL or SPL tokens; proposals carry quantity only."
)


@dataclass(frozen=True)
class ScanRequest:
    btc_addresses: tuple[str, ...] = ()
    solana_addresses: tuple[str, ...] = ()
    price_currency: str | None = "USD"
    kinds: tuple[str, ...] = ("holding",)  # add "valuation" to also emit valuation proposals
    include_utxo_count: bool = False
    submit: bool = True


def parse_request(raw: Mapping[str, Any] | None) -> ScanRequest:
    raw = raw or {}
    unknown = set(raw) - {
        "btc_addresses",
        "solana_addresses",
        "price_currency",
        "kinds",
        "include_utxo_count",
        "submit",
    }
    if unknown:
        raise ValidationError(f"unknown request fields: {sorted(unknown)}")

    def _list(name: str) -> tuple[str, ...]:
        value = raw.get(name) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValidationError(f"{name} must be a list of strings")
        if len(value) > MAX_ADDRESSES_PER_CHAIN:
            raise ValidationError(f"at most {MAX_ADDRESSES_PER_CHAIN} {name} per run", code="too_many")
        return tuple(value)

    currency = raw.get("price_currency", "USD")
    if currency is not None and currency not in CURRENCY_EXPONENTS:
        raise ValidationError(f"price_currency must be one of {sorted(CURRENCY_EXPONENTS)} or null")
    kinds = (
        tuple(raw["kinds"])
        if "kinds" in raw and isinstance(raw["kinds"], list)
        else (("holding",) if "kinds" not in raw else ())
    )
    if not set(kinds) <= {"holding", "valuation"} or not kinds:
        raise ValidationError("kinds must be a subset of ['holding', 'valuation']")
    for flag in ("include_utxo_count", "submit"):
        if flag in raw and not isinstance(raw[flag], bool):
            raise ValidationError(f"{flag} must be a boolean")
    return ScanRequest(
        btc_addresses=_list("btc_addresses"),
        solana_addresses=_list("solana_addresses"),
        price_currency=currency,
        kinds=kinds,
        include_utxo_count=bool(raw.get("include_utxo_count", False)),
        submit=bool(raw.get("submit", True)),
    )


def _issue(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _dedupe(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


class _Scan:
    def __init__(self, host: Host, clock: Clock, client: PoliteClient, request: ScanRequest) -> None:
        self.host, self.clock, self.client, self.request = host, clock, client, request
        self.proposals: list[dict[str, Any]] = []
        self.balances: list[dict[str, Any]] = []
        self.issues: list[dict[str, str]] = []
        self._rpc_id = 0
        self.price: dict[str, Any] | None = None
        self._btc_price: btc.BtcPrices | None = None
        self._btc_price_provenance: tuple[str, str] | None = None

    # -- Bitcoin ---------------------------------------------------------
    def run_btc(self) -> None:
        parsed: list[Any] = []
        for raw in _dedupe([a.strip() for a in self.request.btc_addresses]):
            try:
                parsed.append(parse_btc_address(raw))
            except ValidationError as exc:
                self.issues.append(_issue(f"btc_{exc.code}", raw[:80], exc.message))
        if not parsed:
            return
        if self.request.price_currency:
            self._load_btc_price()
        for info in parsed:
            self._btc_one(info.normalized, info.kind)

    def _load_btc_price(self) -> None:
        currency = self.request.price_currency
        try:
            response = self.client.get(btc.url_for(btc.MEMPOOL_HOST, btc.PRICES_PATH))
            prices = btc.parse_prices_response(response.body)
            if currency not in prices.prices:
                raise ValidationError(f"service did not quote {currency}", code="price_currency_missing")
        except CoreError as exc:
            self.issues.append(
                _issue("btc_price_unavailable", btc.MEMPOOL_HOST, f"{exc.message}; proposing quantity only")
            )
            return
        self._btc_price = prices
        self._btc_price_provenance = (btc.MEMPOOL_HOST, btc.PRICES_PATH)
        self.price = {
            "asset": "BTC",
            "currency": currency,
            "origin": btc.MEMPOOL_HOST,
            "reference": btc.PRICES_PATH,
            "service_time": prices.time,
            "fetched_at": iso_utc(self.clock.utcnow()),
        }

    def _btc_one(self, address: str, kind: str) -> None:
        failures: list[str] = []
        for source in btc.SOURCES:
            try:
                response = self.client.get(btc.url_for(source, btc.address_path(address)))
                balance = btc.parse_address_response(response.body, address)
            except CoreError as exc:
                failures.append(f"{source}: {exc.code}")
                continue
            fetched = self.clock.utcnow()
            utxo_count = self._utxo_count(source, address)
            self._btc_record(source, address, kind, balance, fetched, utxo_count)
            return
        self.issues.append(_issue("btc_fetch_failed", short_address(address), "; ".join(failures)))

    def _utxo_count(self, source: str, address: str) -> int | None:
        if not self.request.include_utxo_count:
            return None
        try:
            response = self.client.get(btc.url_for(source, btc.utxo_path(address)))
            return len(btc.parse_utxo_response(response.body))
        except CoreError as exc:
            self.issues.append(_issue("btc_utxo_unavailable", short_address(address), exc.code))
            return None

    def _btc_record(
        self, source: str, address: str, kind: str, balance: btc.BtcBalance, fetched: Any, utxo_count: int | None
    ) -> None:
        quantity = format_units(balance.confirmed_sats, 8)
        record: dict[str, Any] = {
            "chain": "bitcoin",
            "address": address,
            "address_kind": kind,
            "asset": "BTC",
            "quantity": quantity,
            "unit": "BTC",
            "unconfirmed_delta_sats": balance.unconfirmed_delta_sats,
            "tx_count": balance.tx_count,
            "origin": source,
            "fetched_at": iso_utc(fetched),
        }
        if utxo_count is not None:
            record["utxo_count"] = utxo_count
        value: str | None = None
        currency = self.request.price_currency
        if self._btc_price is not None and currency:
            value = fiat_value(balance.confirmed_sats, 8, self._btc_price.prices[currency], currency)
            record["value"] = {"amount": value, "currency": currency}
        self.balances.append(record)
        provenance = Provenance(
            origin=source,
            reference=f"{btc.address_path(address)} (confirmed = funded - spent)",
            fetched_at=iso_utc(fetched),
            price_origin=btc.MEMPOOL_HOST if value is not None else None,
        )
        key = btc_source_key(address)
        name = f"Bitcoin wallet ({short_address(address)})"
        if "holding" in self.request.kinds:
            self.proposals.append(
                holding(
                    source_key=key,
                    display_name=name,
                    symbol="BTC",
                    chain="bitcoin",
                    quantity=quantity,
                    unit="BTC",
                    as_of=fetched,
                    provenance=provenance,
                    value=value,
                    currency=currency if value else None,
                )
            )
        if "valuation" in self.request.kinds and value is not None and currency:
            self.proposals.append(
                valuation(
                    source_key=key,
                    value=value,
                    currency=currency,
                    as_of=fetched,
                    provenance=provenance,
                )
            )

    # -- Solana ----------------------------------------------------------
    def _rpc(self, body_builder: Callable[[int], str]) -> tuple[dict[str, Any], int]:
        self._rpc_id += 1
        rid = self._rpc_id
        response = self.client.post_json(solana.RPC_URL, body_builder(rid))
        return solana.parse_envelope(response.body, rid), rid

    def run_solana(self) -> None:
        addresses: list[str] = []
        for raw in _dedupe([a.strip() for a in self.request.solana_addresses]):
            try:
                addresses.append(parse_solana_address(raw))
            except ValidationError as exc:
                self.issues.append(_issue(f"sol_{exc.code}", raw[:80], exc.message))
        sol_balances: dict[str, solana.SolBalance] = {}
        for start in range(0, len(addresses), solana.MAX_ACCOUNTS_PER_CALL):
            chunk = addresses[start : start + solana.MAX_ACCOUNTS_PER_CALL]
            try:
                result, _ = self._rpc(lambda rid, c=chunk: solana.build_get_multiple_accounts(c, rid))
                for bal in solana.parse_multiple_accounts(result, chunk):
                    sol_balances[bal.address] = bal
            except CoreError as exc:
                for address in chunk:
                    self.issues.append(_issue("sol_fetch_failed", short_address(address), exc.code))
        fetched = self.clock.utcnow()
        for address in addresses:
            if address in sol_balances:
                self._sol_native(sol_balances[address], fetched)
            self._sol_tokens(address)

    def _sol_provenance(self, method: str, slot: int, address: str, fetched: Any) -> Provenance:
        return Provenance(
            origin=solana.RPC_HOST,
            reference=f"JSON-RPC {method} {short_address(address)} at slot {slot}, finalized",
            fetched_at=iso_utc(fetched),
        )

    def _sol_native(self, bal: solana.SolBalance, fetched: Any) -> None:
        quantity = format_units(bal.lamports, solana.LAMPORT_DECIMALS)
        self.balances.append(
            {
                "chain": "solana",
                "address": bal.address,
                "asset": "SOL",
                "quantity": quantity,
                "unit": "SOL",
                "account_exists": bal.account_exists,
                "slot": bal.slot,
                "origin": solana.RPC_HOST,
                "fetched_at": iso_utc(fetched),
            }
        )
        if "holding" in self.request.kinds:
            self.proposals.append(
                holding(
                    source_key=sol_source_key(bal.address),
                    display_name=f"Solana wallet ({short_address(bal.address)})",
                    symbol="SOL",
                    chain="solana",
                    quantity=quantity,
                    unit="SOL",
                    as_of=fetched,
                    provenance=self._sol_provenance("getMultipleAccounts", bal.slot, bal.address, fetched),
                )
            )

    def _sol_tokens(self, address: str) -> None:
        accounts: list[solana.TokenAccount] = []
        slot = 0
        for program in solana.TOKEN_PROGRAMS:
            try:
                result, _ = self._rpc(lambda rid, p=program: solana.build_get_token_accounts_by_owner(address, p, rid))
                found, slot = solana.parse_token_accounts(result, address)
                accounts.extend(found)
            except CoreError as exc:
                self.issues.append(_issue("sol_tokens_failed", short_address(address), f"{program[:8]}: {exc.code}"))
                return  # partial token data would understate holdings; propose none for this wallet
        fetched = self.clock.utcnow()
        try:
            holdings = solana.aggregate_by_mint(accounts)
        except CoreError as exc:
            self.issues.append(_issue("sol_tokens_failed", short_address(address), exc.code))
            return
        for item in holdings:
            if item.decimals > 18:
                self.issues.append(
                    _issue("sol_token_decimals", item.mint, "decimals > 18 exceeds the proposal quantity scale")
                )
                continue
            quantity = format_units(item.amount, item.decimals)
            self.balances.append(
                {
                    "chain": "solana",
                    "address": address,
                    "asset": item.mint,
                    "quantity": quantity,
                    "unit": f"mint:{short_address(item.mint)}",
                    "decimals": item.decimals,
                    "token_accounts": item.accounts,
                    "slot": slot,
                    "origin": solana.RPC_HOST,
                    "fetched_at": iso_utc(fetched),
                }
            )
            if "holding" in self.request.kinds:
                self.proposals.append(
                    holding(
                        source_key=sol_source_key(address, item.mint),
                        display_name=f"SPL token {short_address(item.mint)} ({short_address(address)})",
                        symbol=None,
                        chain="solana",
                        quantity=quantity,
                        unit=f"mint:{item.mint}",
                        as_of=fetched,
                        provenance=self._sol_provenance("getTokenAccountsByOwner", slot, address, fetched),
                    )
                )


def scan(
    host: Host,
    request: ScanRequest,
    clock: Clock | None = None,
    *,
    rand: Callable[[], float] = random.random,
    policies: Mapping[str, OriginPolicy] | None = None,
    cache_ttl_s: float = 60.0,
) -> dict[str, Any]:
    clock = clock or SystemClock()
    client = PoliteClient(host, clock, policies or DEFAULT_POLICIES, cache_ttl_s=cache_ttl_s, rand=rand)
    run = _Scan(host, clock, client, request)
    run.run_btc()
    run.run_solana()

    valid: list[dict[str, Any]] = []
    now = clock.utcnow()
    for proposal in run.proposals:
        try:
            validate_proposal(proposal, now=now, allowed_kinds=(ASSET_KIND,))
            valid.append(proposal)
        except ValidationError as exc:
            run.issues.append(_issue(f"proposal_{exc.code}", proposal.get("source_key", "?")[:80], exc.message))

    batch_list = batches(valid)
    submission: dict[str, Any] = {"status": "not_requested", "receipts": []}
    if request.submit and batch_list:
        submission = _submit(host, batch_list)
    elif request.submit:
        submission = {"status": "nothing_to_submit", "receipts": []}

    notes: list[str] = []
    if request.solana_addresses:
        notes.append(NO_SOLANA_PRICE_NOTE)
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": iso_utc(clock.utcnow()),
        "balances": run.balances,
        "price": run.price,
        "proposals": batch_list,
        "submission": submission,
        "issues": run.issues,
        "notes": notes,
        "requests_sent": client.requests_sent,
    }
    assert_no_floats(report)
    return report


def _submit(host: Host, batch_list: list[dict[str, Any]]) -> dict[str, Any]:
    receipts: list[Any] = []
    for batch in batch_list:
        try:
            receipts.extend(host.propose(batch))
        except HostUnsupported as exc:
            return {
                "status": "host_unsupported",
                "message": exc.message + "; proposals are returned in this report instead",
                "receipts": [],
            }
        except CoreError as exc:
            return {"status": "failed", "message": exc.message, "code": exc.code, "receipts": receipts}
    return {"status": "submitted", "receipts": receipts}
