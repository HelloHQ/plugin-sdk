# Bitcoin and Solana wallet tracker - Tier-1 Python sidecar (Wave 1)

Reads the **public** balances of Bitcoin addresses and Solana addresses the
person enters, and turns them into **proposals** (a holding per chain, address
and asset, with quantity, provenance and, for BTC only, an optional fiat
value). The plugin never sees item ids and never writes anything: the person
approves each proposal in the app.

> **Status: pending host support.** The two permissions this plugin needs,
> `propose:holdings` and `propose:valuations`, are designed on the host side
> but **not built**. Until they ship, the plugin cannot submit proposals,
> `manifest.json` will not pass the registry schema (its permission-id enum
> does not contain them), and nothing here is publishable. What works today is
> everything up to the proposal batch, tested against fixtures and a recording
> fake host. See [Blocked on host support](#blocked-on-host-support).

## What works today

| Capability | State |
|---|---|
| BTC address validation: P2PKH, P2SH, P2WPKH, P2WSH (bech32), P2TR (bech32m), mainnet only, checksums verified | works, tested with BIP-173 / BIP-350 vectors |
| Solana address validation (base58, 32 bytes) | works |
| mempool.space and Blockstream Esplora: request builders, parsers (address stats, UTXO list) | works, fixtures from documented shapes |
| Solana public RPC: `getMultipleAccounts` (<= 100 addresses per call), `getTokenAccountsByOwner` (Token and Token-2022 programs, `jsonParsed`), aggregation per mint | works, fixtures from documented shapes |
| Exact amounts: satoshis, lamports and raw token units as integers, fixed-scale decimal strings, no floats | works |
| BTC fiat valuation = quantity x price from `mempool.space/api/v1/prices`, rounded once (half-even) to the currency's minor unit | works, but the price response **shape is unverified** (see below) |
| Sequential, paced, cached requests with jittered exponential backoff and `Retry-After` (fake-clock tested) | works |
| Proposal construction with provenance on every proposal, pre-flight validation, host-sized batching (<= 200 proposals, <= 50 holdings per batch) | works |
| Submitting proposals to the host | **blocked** (no host API) - the sidecar returns the batches as data and reports `submission.status = "host_unsupported"` |
| Sidecar packaging (`build.sh` -> one-file `dist/plugin.py`) | works |
| Install / registry publish / display of results | **blocked** (plugin system production hold, signing, no UI half here) |

## Blocked on host support

1. **`propose:holdings` / `propose:valuations`** and the call that submits a
   batch. Nothing like it exists in the SDK, the protocol or the mock host, and
   this example deliberately does not invent it: `SdkHost.propose` raises
   `HostUnsupported`. The batch shape in `wallet_tracker/proposals.py`
   (`hellohq.proposal-batch@1`) follows the host-side design and **may change**
   when the host ships it. When it exists, the only change needed is
   `SdkHost.propose` (about five lines) plus re-checking the field rules.
2. **Manifest acceptance.** `manifest.json` declares the two `propose:*`
   permissions with `scope.kinds = ["crypto_ticker"]`. The current registry
   schema has no such ids and `additionalProperties: false`, so CI would
   reject it. Declared here so the intended permission set is reviewable.
3. **Verified tier.** A Tier-1 sidecar, `network:fetch` on a sidecar and the
   `propose:*` permissions are all Verified-only, and sidecars are
   desktop-only. The tier is assigned by the registry team; the manifest does
   not set `trust_tier`.
4. **A UI half.** `ui_type` is `headless`; no screen for entering addresses or
   viewing the report is included. Input today is the `scan` call's `args`.
5. **Where entered addresses are kept.** The plugin holds no state. A real
   product needs per-workspace plugin storage (`plugin:storage`, shared across
   workspaces on the device today) or a host-owned address list. Not added, to
   avoid declaring a permission the plan does not need yet.

## Tier and language choice

Tier-1 Python sidecar, stdlib only, in the style of `fx-advisor`,
`notes-keeper` and `portfolio-analyst`. Reasons:

* `hellohq_plugin_sdk.host.fetch` is the only ready-made outbound HTTP helper
  in the SDKs. The Rust, Go and JS component SDKs import `wasi:http` in their
  WIT but expose no fetch helper, and the Tier-2 `wasi:http` path is reported to
  drop request bodies today (Solana JSON-RPC is a POST). I could not verify
  that last point from this repo.
* The pure core has no SDK dependency. Only `plugin.py` and `sdk_host.py`
  touch it, so porting to a Tier-2 component later means rewriting those two
  files and the HTTP shim, not the parsers.
* Python makes exact decimal arithmetic (`decimal`) and tests cheap.

The Python SDK is a dev dependency only (path dependency in `pyproject.toml`).
The shipped artifact is one file: `bundle.py` inlines the package and
`plugin.py` into `dist/plugin.py` (the registry's sidecar artifact is a single
file). `tests/test_bundle.py` checks the bundle imports and runs without the
package.

## Layout

```
plugin.py                 sidecar entry (thin adapter, SDK imports live here)
wallet_tracker/
  host.py                 narrow Host interface: fetch + propose; Clock
  sdk_host.py             production Host over hellohq_plugin_sdk
  addresses.py            BTC + Solana validation (base58check, bech32/bech32m)
  btc.py                  mempool.space / Esplora builders + parsers
  solana.py               JSON-RPC builders + parsers
  money.py                exact decimal helpers, rounding, no-float guard
  ratelimit.py, polite.py sliding-window limiter, backoff, cache, allowlist
  proposals.py            proposal builders, validation, provenance, batching
  tracker.py              orchestration: scan(host, request, clock)
bundle.py, build.sh       single-file artifact
tests/                    fixtures + 240+ tests, no network
```

`ratelimit.py`, `polite.py`, `errors.py`, `host.py` and `sdk_host.py` are
copied (package-renamed) into `examples/macro-context`. Examples cannot share
code today (each ships as one file, and this change touches no shared
directory), so the copies are intentional.

## Sources, limits and how the plugin respects them

| Source | Used for | Documented limit / terms | Behaviour |
|---|---|---|---|
| mempool.space REST (`/api/address/:a`, `/api/address/:a/utxo`, `/api/v1/prices`) | BTC balance, optional UTXO count, BTC price | Over-limit requests get HTTP 429; "repeatedly exceed the limits, you may be banned"; no numbers published | <= 10 requests / 10 s and >= 1 s apart (our own conservative choice), one in flight at a time, 60 s cache, full-jitter backoff (1 s base, 30 s cap, 3 retries), `Retry-After` honoured |
| Blockstream Esplora (`blockstream.info/api`) | BTC fallback when mempool.space fails | The reviewed Esplora docs state no limit | Same pacing; used only after mempool.space fails or returns an unparsable body. The origin actually used is recorded in provenance |
| Solana public RPC `api.mainnet.solana.com` | SOL and SPL balances | 100 requests / 10 s / IP, 40 / 10 s for one method; "not intended for production applications" | <= 20 requests / 10 s and >= 0.5 s apart; one `getMultipleAccounts` per 100 addresses; two `getTokenAccountsByOwner` calls per wallet (cannot be batched across owners). `finalized` commitment. **Caveat: the public endpoint is not for production use; a real deployment needs a user-chosen or dedicated RPC, which is a host-credential question, not solved here.** |

No API keys, cookies or auth headers are sent. Only the three declared origins
can be contacted, only over HTTPS (`PoliteClient` enforces this in addition to
the host's allowlist). No scraping.

## Money rules

* Chain amounts are integers (satoshis, lamports, raw token units) and become
  decimal **strings** by integer arithmetic only, at fixed scale: BTC 8 dp, SOL
  9 dp, tokens at their own `decimals` (<= 18; a token with more is reported as
  an issue and not proposed). Quantities are exact and never rounded.
* Vendor JSON is parsed with `parse_float=Decimal`. `uiAmount` (a float in
  Solana's reply) is ignored; the integer string `amount` is used.
* A valuation is `quantity x price` computed exactly and **rounded once**, to
  the ISO 4217 minor unit of the requested currency (USD, EUR, GBP, CAD, CHF,
  AUD: 2 dp; JPY: 0 dp) with `ROUND_HALF_EVEN`. Prices outside (0, 1e15] are
  parse errors.
* `assert_no_floats` runs over every proposal and the whole report.

## Addresses and proposals

* Confirmed BTC balance = `chain_stats.funded_txo_sum - chain_stats.spent_txo_sum`.
  The unconfirmed delta is reported but never proposed.
* Solana: native SOL via `getMultipleAccounts` (a `null` account is reported as
  0 SOL with `account_exists = false`); SPL tokens summed per mint across both
  token programs; zero totals are dropped.
* One `holding` proposal per (chain, address, asset), keyed by a source key:
  `btc:address:<addr>`, `sol:address:<addr>`, `sol:address:<addr>:mint:<mint>`.
  `kinds: ["holding", "valuation"]` additionally emits a `valuation` for priced
  assets; default is holdings only (whether a repeat run should send holdings
  or valuations once an item is bound is an open question below).
* **Every proposal carries provenance**: `source.origin` (host actually
  contacted), `source.reference` (request path or RPC method + slot),
  `source.fetched_at`, plus the identifier (`source_key`) and, when priced,
  `price_origin`. Validation fails closed without them.
* **Prices.** BTC: `mempool.space/api/v1/prices` is keyless and documented as an
  endpoint, so BTC holdings get a `value` in the chosen currency
  (`price_currency`, default USD; `null` = quantity only). **SOL and SPL tokens
  have no keyless price source documented in what was reviewed, so they are
  proposed with quantity only**, and the report says so (`notes`). Token
  symbols are not guessed either (`instrument.symbol` is `null`; the mint is
  the identity).
* One address failing never aborts the others; every skip is an entry in
  `issues` with a stable code.

## Unverified response shapes

Fixtures are hand-written from the documentation (URLs are in
`tests/fixtures.py` and the parser docstrings). Where documentation was missing
the parser is defensive: unknown fields ignored, missing or mistyped fields are
an explicit `ParseError`, never a guessed value.

* **`GET /api/v1/prices`**: the mempool.space docs page names the endpoint but
  shows no response example. The parser accepts the shape seen in one live call
  on 2026-10-04 (`{"time", "USD", "EUR", "GBP", "CAD", "CHF", "AUD", "JPY"}`).
  If that shape changes the price degrades to "quantity only" with an issue.
* **Token-2022** (`spl-token-2022`) `jsonParsed` account shape: not found in the
  docs reviewed; accepted only if it has the same fields as `spl-token`.
* The Esplora `GET /address/:a` and `/utxo` shapes are documented by Blockstream;
  mempool.space documents the same field names.

## Build and test

```bash
cd examples/wallet-tracker
uv sync --group dev
uv run pytest --cov=wallet_tracker --cov-report=term-missing
uv run ruff check .
./build.sh            # -> dist/plugin.py + sha256 for manifest.json (placeholder committed)
```

The tests never touch the network (sockets are blocked by an autouse fixture)
and never sleep (a fake clock records waits). They cover valid, invalid, empty
and huge inputs for every parser, good and bad addresses, limiter and backoff
behaviour, provenance on every proposal, no-float money, request shapes
(no auth headers or key parameters), and the bundled artifact.

## Requests for shared files and protocol (not made here)

* Registry schema / protocol: add `propose:holdings`, `propose:valuations` (with
  `scope.kinds`) once the host ships them.
* SDK: a `propose` host call in `sdks/python` (and the NDJSON schema), and a
  `Decimal`-aware JSON helper would remove the `parse_float` boilerplate.
* `README.md` examples index and `.github/workflows/ci.yml`: add this example
  (`uv sync --group dev && uv run pytest && uv run ruff check .` in
  `examples/wallet-tracker`).
* `mock-host`: no propose support; the Dart mock cannot exercise submission.

## Open questions

1. When an item is already bound, should a repeat run send `holding` or
   `valuation` proposals? The plugin cannot know; default is `holding`.
2. Which RPC should replace the public Solana endpoint for real use?
3. Is a user-supplied BTC price currency enough, or should a missing price in
   the chosen currency fall back to another one (currently: quantity only)?
4. A keyless, documented SOL / SPL price source (none was found).
