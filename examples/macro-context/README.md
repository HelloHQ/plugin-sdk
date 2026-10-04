# Macro context - Tier-1 Python sidecar (Wave 1, read-only)

Fetches read-only macroeconomic **context series** from keyless public
sources and returns them with as-of dates, units and provenance: central-bank
policy rates, FX reference rates, inflation and GDP growth indicators, and US
Treasury average interest rates. It states what a source published. It never
gives advice, forecasts or projections, and computes nothing from the values.

The plugin holds one permission, `network:fetch`. It has no `propose:*` or
write permission, and its `Host` interface has no write operation at all.

> **Status: example, not publishable yet.** Nothing here depends on the
> unbuilt propose-only write API, so the core works today against fixtures.
> What blocks a real release is not host write support but the general plugin
> production hold (Verified-tier sidecar, signing, registry) and the missing UI
> half: `ui_type` is `headless` and no screen renders the result. See
> [What is blocked](#what-is-blocked).

## Series (explicit catalogue, no wildcards)

| Source | Series | Category | Unit (basis) |
|---|---|---|---|
| ECB Data Portal | `ecb.policy.mro` (FM `D.U2.EUR.4F.KR.MRR_FR.LEV`), `ecb.policy.dfr` (FM `D.U2.EUR.4F.KR.DFR.LEV`) | policy_rate | from the source's `UNIT` attribute (e.g. "Percent per annum") |
| ECB | `ecb.fx.usd/jpy/sgd/gbp/chf` (EXR `D.<CCY>.EUR.SP00.A`) | fx | source `UNIT` (currency) + note "units of <CCY> per 1 EUR" |
| ECB | `ecb.inflation.hicp` (ICP `M.U2.N.000000.4.ANR`) | inflation | source `UNIT` ("Percentage change") |
| ECB | `ecb.yield.aaa10y` (YC `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y`) | yield | source `UNIT` ("Percent per annum") |
| World Bank Indicators v2 | `wb.FP.CPI.TOTL.ZG.<ISO3>` inflation, `wb.NY.GDP.MKTP.KD.ZG.<ISO3>` real GDP growth; default countries USA, SGP, CHN, EMU (any 3-letter code, <= 6 per run) | inflation, growth | "percent" (catalogue; the indicator names say "(annual %)") |
| US Treasury Fiscal Data | `ust.avg_rate.<security>` from `v2/accounting/od/avg_interest_rates` (Marketable: Bills, Notes, Bonds, ...) | average_interest_rate | "percent" (source `meta.dataTypes` must say PERCENTAGE) |
| MAS via data.gov.sg | `mas.fx.<currency>` from dataset `d_cdd73fd4341b345fa4307e44d6f82175` ("Exchange Rates, (As At End Of Period), Monthly") | fx | "SGD per foreign-currency unit", **multiplier unknown, see below** |

Every series carries `as_of` (the latest observation's period, exactly as
published: a date, month or year), `latest`, up to N observations ascending,
`unit`, `unit_basis` (`source` = stated by the source, `catalog` = stated by
this plugin), `unit_multiplier` (`null` when not stated), `frequency`, and
`provenance` = `{source, identifier, reference, fetched_at}`.

**Treasury caveat.** Fiscal Data publishes *average interest rates on
outstanding securities* (month-end). That is not a market yield and not a par
yield curve; the series titles say so. Fiscal Data has no daily par yield
curve; that comes from another Treasury publication that is **not** on the
roadmap's source list, so it is not used. The only yield-like market series here
is the ECB's AAA euro-area 10-year spot rate.

**MAS / data.gov.sg caveat.** The dataset does not say whether each currency is
quoted per 1 or per 100 units, and MAS's own page lists currencies (e.g.
CHF, AUD) "per 100 units" that the dataset's magnitudes (observed
2026-10-04) do not match. The plugin therefore never converts: it emits the
published number, `unit_multiplier: null` and a note telling the reader to
verify with MAS before using it as SGD per 1 unit. The older daily dataset
`d_046ff8d521a218d9178178cfbfc45c2c` stops in October 2015 and is deliberately
not used.

## What works today

Everything in the core: request builders, strict parsers, unit handling,
politeness (pacing, cache, backoff), orchestration into one report, and the
bundled single-file sidecar. All tested against hand-written fixtures and a
fake host; nothing needs the network.

`plugin.py` exposes one function, `context` (default). Optional `args`:
`sections` (subset of `ecb`, `worldbank`, `treasury`, `sgfx`), `ecb_series`
(catalogue ids), `worldbank_countries`, `worldbank_indicators`,
`treasury_months` (1-60, default 12), `fx_currencies` (`DataSeries` names, default
US Dollar, Euro, Sterling Pound, Japanese Yen, Renminbi), `fx_months` (1-24,
default 6). Unknown fields are rejected. One failing series does not abort the
others; each failure is an entry in `issues` with a stable code (`no_data`,
`parse_error`, `worldbank_error`, `rate_limited`, `permission_denied`, ...).

## What is blocked

1. **Plugin production readiness** (publisher signing, real manifest hashes,
   `plugins` feature hold). Not specific to this plugin.
2. **Verified tier + desktop.** A sidecar with `network:fetch` is Verified-only
   and desktop-only; mobile would need a Tier-2 port (the pure core ports; only
   `plugin.py`, `sdk_host.py` and the HTTP shim are SDK-bound).
3. **A UI half.** The result is plain JSON for a declarative or WebView UI that
   does not exist yet.
4. **Scheduling.** The plugin runs when called; there is no host-side schedule
   or refresh mechanism, and no plugin storage is used (the in-memory cache
   lives for one run).

It does **not** depend on `propose:holdings` / `propose:valuations`.

## Sources, limits and terms

| Source | Key / registration | Documented limit | Behaviour |
|---|---|---|---|
| ECB Data Portal `data-api.ecb.europa.eu` | none | none published | <= 10 req / 10 s, >= 1 s apart (our choice), 1 h cache, backoff |
| World Bank `api.worldbank.org` | none ("API keys ... no longer necessary") | none documented | same pacing |
| US Treasury Fiscal Data `api.fiscaldata.treasury.gov` | none ("does not require a user account or registration for a token") | none documented | same pacing |
| data.gov.sg datastore | none (unauthenticated) | Datastore Search: 4 req / 10 s unauthenticated | <= 3 req / 10 s and >= 3 s apart; one request per run |

Full-jitter exponential backoff (1 s base, 30 s cap, 3 retries) on 429 / 5xx and
transport timeouts; `Retry-After` (seconds) honoured as a capped lower bound.
Only the four declared origins over HTTPS; no API keys, cookies or auth
headers; no scraping. Attribute the sources when displaying the data (the ECB,
World Bank, US Treasury and MAS / Government of Singapore are the publishers;
check each source's reuse terms before shipping a UI).

## Exact values, no floats

Vendor JSON is parsed with `parse_float=Decimal` and re-emitted as decimal
**strings**, digit-for-digit as published (e.g. `3.5921733451`). Nothing is
rounded, scaled, converted or derived. Floats in an input where a value is
expected (a string where the docs promise a number, for ECB and World Bank) are
rejected, and the whole report is checked with `assert_no_floats`.

## Unverified response shapes

Fixtures are hand-written from documentation (URLs in `tests/fixtures.py` and
the parser docstrings). Parsers ignore unknown fields and raise on missing or
mistyped ones.

* **ECB**: SDMX-JSON 1.0 as specified by `sdmx-twg/sdmx-json`; the ECB help
  pages were unavailable (HTTP 503) when this was written, so the ECB-specific
  parts (series keys, `lastNObservations`, `UNIT` / `UNIT_MULT` attributes) were
  checked once against live responses rather than against the help page.
* **World Bank**: the docs page describes the two-element array but not the
  per-observation field list or the error body; both follow what was seen in
  practice. `date` is required to be a 4-digit year.
* **data.gov.sg**: the CKAN-style `datastore_search` envelope
  (`success`, `result.records`, `result.total`) and the `DataSeries` row labels
  ("US Dollar", "Euro", "Sterling Pound", "Japanese Yen", "Renminbi") are not in
  the documentation reviewed; the labels can change without notice and a
  missing label is an explicit `series_not_found`. The wide `2026Jul` column
  layout is per the dataset page.
* **Treasury**: documented. Missing values may appear as `"null"` strings; those
  are skipped.

## Layout, build and test

```
plugin.py            sidecar entry (thin adapter)
macro_context/       host, sdk_host, ratelimit, polite, errors (copied from
                     wallet-tracker), money, series, ecb, worldbank, treasury,
                     sgfx, context
bundle.py, build.sh  single-file artifact (ecb/worldbank/treasury/sgfx are
                     embedded as separate module objects because they share
                     function names)
tests/               fixtures + 130+ tests, no network, no real sleeping
```

```bash
cd examples/macro-context
uv sync --group dev
uv run pytest --cov=macro_context --cov-report=term-missing
uv run ruff check .
./build.sh
```

Tier-1 Python sidecar for the same reasons as the wallet tracker: the Python SDK
is the only one with a ready `fetch` helper, and the core has no SDK dependency.

## Requests for shared files and protocol (not made here)

* `README.md` examples index and `.github/workflows/ci.yml`: add this example.
* SDK: nothing required. A shared pure-Python helper package (limiter, polite
  client, decimal parsing) would remove the copies between the two examples.

## Open questions

1. Treasury daily par yield curve: add it as a source (it is outside the
   roadmap's list)?
2. How should the UI present `unit_multiplier: null` for the MAS series, or
   should the product pin a verified per-currency multiplier table?
3. Is a monthly end-of-period MAS series enough for the SG segment, or is a
   daily MAS source needed (the keyless daily dataset on data.gov.sg ended in
   2015)?
4. Where do refreshes get scheduled?
