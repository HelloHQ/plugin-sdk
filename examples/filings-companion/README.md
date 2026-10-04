# Holdings Filings Companion - Tier-1 Python sidecar

**Status: core built and tested; manifest is complete but the plugin system is not yet open to
customers.** Information only: for tickers, ISINs, CUSIPs, FIGIs or CIKs **the person enters**,
it shows recent SEC filings and the latest annual XBRL figures exactly as the company reported
them. It is not investment advice or a recommendation, and every output says so.

Sources (keyless, no scraping, no company credentials):
SEC EDGAR (`data.sec.gov` submissions, company facts, company concept; plus the documented
`www.sec.gov/files/company_tickers.json` ticker file) and OpenFIGI (`api.openfigi.com`) for
ISIN/CUSIP/FIGI to ticker mapping.

## What works today

Everything, against fixtures and the in-memory fake host (`tests/fakes.py`): identifier
validation (CIK padding, ISIN Luhn check, CUSIP check digit), the contact/User-Agent gate, the
SEC and OpenFIGI limiters, request building, response parsing (submissions, XBRL company facts
and concept, ticker file, OpenFIGI batch responses), identifier resolution, provenance, and the
adapter. `python3 -m pytest` runs 121 tests with no network; `ruff check .` and
`ruff format --check .` are clean.

## Blocked on the host

This plugin needs **no** propose permission: it writes nothing to the person's data, so the
host's propose-only write API (designed, not built) is not required. What it does depend on:

* **Plugin system availability**: plugins are held back from customers; Tier-1 sidecars need
  the Verified tier and desktop, and `network:fetch` from a sidecar is Verified-tier only.
* **Setting the SEC `User-Agent`**: the SEC requires a declared contact. The core sends it as a
  plain `User-Agent` header, but the host may strip or reject plugin-set headers (a header
  allowlist is planned). Until the host lets a manifest-declared, non-secret `User-Agent`
  through, live SEC calls may be refused; see "Requests" below.
* **Plugins cannot read holdings** (only summaries), so identifiers are typed in by the person
  or kept by the UI in plugin storage; the plugin cannot discover them itself.
* **8 MiB response cap**: very large filers' company-facts files can exceed it. The core then
  falls back to the small per-concept endpoints automatically (code `response_too_large`
  raised by the transport). The real host's oversize error shape is unverified, so the fallback
  currently triggers on the transport's own size check.

## Using it (sidecar functions)

```jsonc
// 1. once: the SEC requires every automated client to identify itself
{ "function": "configure_contact", "args": { "contact": "Your Name you@yourdomain.com" } }
// 2. look up up to 10 identifiers per call
{ "function": "lookup", "args": {
    "identifiers": [ { "type": "ticker", "value": "BRK.B" },
                     { "type": "isin",   "value": "US0378331005" },
                     { "type": "cik",    "value": "320193" } ],
    "forms": ["10-K", "10-Q", "8-K"], "filings_limit": 5, "include_facts": true } }
```

The **contact is never hard-coded**: it lives in plugin storage, is validated (an email is
required; control characters, placeholders and reserved example domains are rejected), and
**every SEC request is refused before it is sent** while no valid contact exists. The contact is
sent only to SEC origins, never to OpenFIGI.

Per identifier the result has: `status` (`ok`, `unresolved`, `error`, `not_attempted`), how it
was resolved (`via`), company name/CIK, recent filings (accession, form, dates, a display link
that is never fetched), key annual facts (revenue, net income, assets, liabilities, equity,
diluted EPS, cover-page shares outstanding) with the unit, period, form, filing date and
accession number, an explicit `facts_unavailable` list, and `sources` (origin, URL, fetch time
and the identifier used for each call).

## Rules this plugin follows

* **SEC**: only `data.sec.gov` plus the documented ticker file on `www.sec.gov`; a descriptive
  `User-Agent` with the person's contact; a client-side limiter of **8 requests/s** (the
  constructor refuses anything above the SEC's 10/s maximum), shared by both SEC origins.
  Redirects are not followed.
* **OpenFIGI**: **25 requests/minute** keyless (limiter window padded to 60.5 s) and **at most 10
  jobs per request**; all lookups in a call are batched; a 429 is surfaced with the
  `ratelimit-reset` hint instead of blind retries. Bare tickers are mapped on US listings only,
  and ISIN/CUSIP/FIGI results are accepted only for a single US-listed ticker (non-US or
  ambiguous listings stay `unresolved`, never guessed).
* **Facts**: taken as filed (latest fiscal-year value from an annual form such as 10-K, picking
  the latest period end and then the latest filing, so amendments win). Nothing is derived,
  converted or adjusted. Values are `Decimal` strings (JSON is parsed with `parse_float=Decimal`;
  floats are never produced), with the unit alongside.
* **No scraping**: filing document links are built for display only and never requested.

## Verification status of response shapes

Documented on the SEC API page: the URL patterns and the 10-digit CIK. Checked against live
responses (the page does not list the fields): `companyconcept` units entries (`end`, `val`,
`accn`, `fy`, `fp`, `form`, `filed`, `frame`, optional `start`) and `submissions`
`filings.recent` parallel arrays. **Not verified from documentation** (parsed defensively; a
missing field is an explicit `unexpected_response_shape` error): the `companyfacts` document
(`facts` > taxonomy > tag > `units`), `company_tickers.json` (`cik_str`, `ticker`, `title`),
and which optional `submissions` fields exist. OpenFIGI shapes follow its documentation page.
The OpenFIGI parser was run once by hand against a live keyless response (not part of the test
suite); the SEC endpoints could not be live-checked here because that needs a real contact in
the User-Agent. The OpenFIGI terms of use and any attribution requirement were **not reviewed**.

## Why Python Tier 1

Same reasoning as the other networked examples: the Python SDK is the only one with a `fetch`
host call, and `decimal`/`json` make exact, float-free XBRL handling straightforward. The
registry artifact is one `plugin.py`, so `bundle.py` concatenates the tested `filings_companion`
package and the thin adapter (a test executes the bundle and checks for name collisions).

## Layout

```
plugin.py                 thin sidecar adapter
filings_companion/        pure core: identifiers, contact, ratelimit, transport, sec, openfigi,
                          service, hostapi (narrow Host: fetch, storage, clock, sleep)
bundle.py, build.sh       single-file bundle -> dist/plugin.py (+ sha256 for the manifest)
manifest.json             network:fetch (data.sec.gov, www.sec.gov, api.openfigi.com), plugin:storage
tests/                    fixtures from documented shapes (fictional company), fakes, tests
```

Manifest checked against the registry's current `manifest.schema.json`: valid. `trust_tier` is
not set (registry-assigned); the artifact hash is the usual placeholder.

## Requests (not made here; this change touches only this directory)

* Host: allow a manifest-declared, non-secret `User-Agent` (and nothing else) on SEC requests.
* Host: a documented, typed error for "response over the 8 MiB cap" so the fallback can key on it.

## Run

```bash
python3 -m pytest          # 121 tests, no network (sockets are blocked in tests)
ruff check . && ruff format --check .
./build.sh                 # writes dist/plugin.py and prints its sha256
```
