# Property Value Estimates - Tier-1 Python sidecar

**Status: core built and tested; manifest is "pending host support".** Information only:
this plugin summarises recent *comparable sales* (median and percentiles) for an area and
property type the person chooses. It is **not a valuation, appraisal or advice**, and every
output says so.

| Region | Source | How it is read |
|---|---|---|
| Singapore (`sg_hdb`) | data.gov.sg dataset "Resale flat prices based on registration date from Jan-2017 onwards" | documented `datastore_search` API, 100 rows per page |
| England and Wales (`uk_ppd`) | HM Land Registry Price Paid Data | Linked Data JSON API, or a Price Paid CSV the person provides |
| France (`fr_dvf`) | DVF, geolocated CSV per commune and year (Etalab) | fetched from `files.data.gouv.fr` (follows its one redirect to object storage) |
| Ireland (`ie_ppr`) | Property Services Regulatory Authority, Residential Property Price Register | **CSV the person downloads and provides**; never fetched |

## What works today

Everything in the pure core, against fixtures and an in-memory fake host (`tests/fakes.py`):
query builders, response and CSV parsers, normalisation, the estimator, validation,
provenance, attribution, the rate limiter, and building (not sending) a propose-only valuation.
`python3 -m pytest` runs 136 tests with no network; `ruff check .` and `ruff format --check .`
are clean.

## Blocked on the host ("pending host support")

* **`propose:valuations`**: the host's propose-only write API is designed but not built.
  `manifest.json` declares the permission (`kinds: ["home"]`) so reviewers see the intent, but
  today's registry schema does not accept it (see "Manifest" below). The adapter's `propose`
  function builds the proposal and then fails with the code `pending_host_support`; nothing is
  sent. No SDK, ABI or protocol change was made to fake it.
* **`read:external_input`** (planned permission, `csv` files): needed so the person can pick the
  Ireland PPR CSV (or a UK Price Paid CSV). Until it exists the CSV text must come from the UI
  as `csv_text`.
* **Redirects**: `files.data.gouv.fr` answers `302` to `geo-dvf.s3.sbg.io.cloud.ovh.net`. The
  host does not follow redirects, so the plugin follows exactly one itself, only to a declared
  origin (that is why that second origin is in the manifest).
* **Response cap**: the host caps responses at 8 MiB. Big communes may exceed it (the call
  fails cleanly), and UK yearly CSVs (115-230 MB) can never be fetched, hence the
  person-provided CSV path.

## Using it (sidecar functions)

`estimate` with `{region, ...}`, add `include_proposal_preview: true` to see the proposal that
would be sent:

```jsonc
{ "region": "sg_hdb", "town": "BISHAN", "flat_type": "4 ROOM", "months": 12 }
{ "region": "uk_ppd", "district": "LEEDS", "town": "LEEDS", "property_type": "T", "months": 12 }
{ "region": "uk_ppd", "district": "LEEDS", "csv_text": "<Price Paid CSV text>" }
{ "region": "fr_dvf", "commune": "75101", "property_type": "apartment", "years": [2024, 2025] }
{ "region": "ie_ppr", "county": "Dublin", "dwelling": "second_hand", "csv_text": "<PPR CSV text>" }
```

## Rules this plugin follows

**Minimum-sample rule.** After removing outliers (Tukey fences, 1.5 x IQR, on price):

* fewer than **10** usable sales: status `insufficient_sample`, **no figure at all**;
* p10 / p90 only with **20** or more; a value may be **proposed** only with **30** or more;
* confidence: 10-29 low, 30-99 medium, 100+ high, lowered one step if the newest sale is more
  than 12 months old; the note always says size, condition, floor, lease and exact location are
  not adjusted for.
* a proposal is a *coarsened* median (nearest 1,000 at 100,000 and above, else nearest 100),
  proposed against the plugin's own source key (for example `sg_hdb:flat_type:4-ROOM:town:BISHAN`).
  The plugin never learns or names an item id and never writes.

**Money.** Integer minor units and decimal strings only; no binary floats anywhere (JSON is
parsed with `parse_float=Decimal`, floats are rejected as input). Rounding: exact `Fraction`
arithmetic, rounded **once**, half-even, to 2 decimals.

**Aggregate only.** Per-sale data is reduced at parse time to date, price and (where present)
floor area. Addresses, postcodes, parcel ids, coordinates and transaction ids are never kept.
Output carries no individual sale: no min, max or list; dates are month-granular.

**Provenance on every result:** source origin, `fetched_at` (UTC), the exact request URLs (or
"person-provided CSV"), the query, sample size, month range, rows skipped by reason, and the
attribution text. **Rate limits** (client-side, injected clock, tested on a fake clock):
data.gov.sg 4 requests / 10 s (limiter window padded to 10.5 s); Land Registry and the French
hosts publish no limit, so 2 requests / s courtesy limit.

## Attribution and terms

* UK: *"Contains HM Land Registry data (c) Crown copyright and database right <year>. This data
  is licensed under the Open Government Licence v3.0."* is in every UK result. The year is the
  fetch year (the published example says 2021; the statement's year rule is unverified). The
  Price Paid pages also say the address data comes from Ordnance Survey/Royal Mail and may only
  be used for personal/non-commercial purposes or to display residential price information, which
  is another reason this plugin outputs no addresses.
* Singapore: Singapore Open Data Licence attribution (exact required wording unverified).
* France: Licence Ouverte 2.0 attribution to DGFiP / Etalab.
* Ireland: PSRA re-use terms (psr.ie/re-use-of-public-sector-information) allow re-use with
  acknowledgement of source and PSRA copyright, accurate reproduction, no misleading use, and
  no use principally to advertise a product or service. Those conditions are met by the
  attribution plus the information-only label. The terms say nothing about automated download,
  and the download page is a POST web form (county and year required) on a Lotus Domino
  application with no documented API or stable file URL, so **it is not fetched**.

Parsers were also run once by hand against live responses (Singapore, UK Linked Data API,
France via the redirect) to confirm the fixtures match reality; that check is not part of the
test suite (tests never use the network).

## Unverified items (parsers are defensive; a missing field is an explicit error, never a guess)

* **France DVF re-identification limits.** The data.gouv.fr DVF page says the data contains
  personal data, that reusers must prevent indirect re-identification, and that reuse must not
  permit indexing by external search engines; the roadmap marked these limits unverified and
  they remain **legally unreviewed**. This plugin is therefore aggregate-only and applies the
  minimum-sample rule, but a legal read is still needed before shipping the France region.
* **UK Linked Data API shape**: the documentation page could not be read; the shape (items,
  `pricePaid`, `transactionDate` like `Fri, 17 May 1996`, `propertyType`,
  `transactionCategory`, `next`, 200-item pages) was **observed from live responses**.
* **DVF file path** (`/geo-dvf/latest/csv/<year>/communes/<dep>/<insee>.csv`) was observed from
  the live site; column names are from the dataset page.
* **Ireland PPR CSV columns** are not specified in anything fetched; columns are found by name,
  and `Date of Sale`, `County` and `Price` are required. The price format (euro sign,
  thousands commas) is from third-party descriptions; mojibake euro signs are tolerated.
* **data.gov.sg page size**: documented maximum 100 (the live API accepted more; we use 100).

## Why Python Tier 1 (language and tier)

It follows the existing networked examples (`fx-advisor`, `portfolio-analyst`): the Python SDK
is the only one with a `fetch` host call today, `decimal`/`fractions`/`csv` make exact money and
bulk parsing simple, and the future propose call is also shaped around the Python SDK. Costs:
Tier 1 needs the Verified tier and desktop only, and the registry artifact is one `plugin.py`,
so `bundle.py` concatenates the tested package plus the thin adapter (a test executes the
bundle). A Tier-2 Rust/Go port would be possible once a world carries both `wasi:http` and
host calls.

## Layout

```
plugin.py                 thin sidecar adapter (SDK host calls -> narrow Host interface)
property_estimates/       pure core: money, ratelimit, stats, estimate, transport, one module
                          per region, proposal, service, hostapi (the Host interface)
bundle.py, build.sh       single-file bundle -> dist/plugin.py (+ sha256 for the manifest)
manifest.json             exact origins, permissions (propose/read_external_input pending)
tests/                    fixtures from documented shapes (synthetic addresses), fakes, tests
```

## Manifest

Permissions: `network:fetch` (origins `data.gov.sg`, `landregistry.data.gov.uk`,
`files.data.gouv.fr`, `geo-dvf.s3.sbg.io.cloud.ovh.net`; no wildcards), `read:external_input`
(csv), `propose:valuations` (kinds `home`). Checked against the registry's current
`manifest.schema.json`: everything validates **except** that `propose:valuations` is not yet in
its permission-id enum (expected, host support pending). `trust_tier` is not set (registry-assigned);
the artifact hash is the usual placeholder.

## Run

```bash
python3 -m pytest          # 136 tests, no network (sockets are blocked in tests)
ruff check . && ruff format --check .
./build.sh                 # writes dist/plugin.py and prints its sha256
```
