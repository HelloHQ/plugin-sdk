# hellohq-plugin-sdk (Python)

Build Tier 1 (Python) HelloHQ plugins. Tier 1 runs inside Pyodide on a Deno
sidecar, so the full scientific stack (NumPy, pandas, scipy, scikit-learn) is
available — desktop only.

## Install

```bash
pip install hellohq-plugin-sdk
```

## Write a plugin

```python
from hellohq_plugin_sdk import serve, UnsupportedFunction

def dispatch(function, args):
    if function == "double":
        return {"value": args[0] * 2}
    raise UnsupportedFunction(function)

serve(dispatch)
```

`serve()` speaks the host's NDJSON protocol on stdin/stdout: it emits `ready`,
answers `ping`, handles `shutdown`, and routes RPC requests to your `dispatch`.
Raise `PluginError(message, code=...)` to return a structured error.

## Data access

The host **pre-fetches** the data your plugin is permitted to read and passes
it in `args` — the sidecar never calls back into the host. Declare the
permissions you need in your manifest (see the registry schema).

## Network fetch

With `network:fetch` (Verified tier) a plugin can call `host.fetch`. The
host only reaches the origins listed on that permission's `scope.origins`
in the manifest, over HTTPS, and does not follow redirects.

```python
from hellohq_plugin_sdk import host

resp = host.fetch("https://api.example.com/rates", headers={"Accept": "application/json"})
resp["status"]          # 200
resp["body"]            # str for a UTF-8 text response, bytes otherwise
resp["body_bytes"]      # always the exact response bytes
resp["body_encoding"]   # "utf8" or "base64": how the host sent it
```

- **Text stays text.** A response whose bytes are valid UTF-8 arrives as a
  `str` in `body`, exactly as in earlier SDK versions.
- **Binary arrives byte-exact.** The host sends any other response (a PDF,
  an image, Latin-1 text) base64-encoded with `body_encoding: "base64"`.
  `fetch` decodes it, so `body` is `bytes`. Code that expects text should
  check `isinstance(resp["body"], str)`. Code that wants bytes either way
  should read `body_bytes`.
- **Unknown `body_encoding`.** If the host sends a value this SDK doesn't
  know, `fetch` raises `PluginError` instead of guessing. Upgrade the SDK if
  you see it.
- **Request headers.** The host forwards only `Accept`, `Accept-Language`,
  `Content-Type`, `If-None-Match` and `If-Modified-Since`
  (`host.ALLOWED_REQUEST_HEADERS`). It drops every other header silently,
  including `Authorization`, `Cookie` and `User-Agent`. Never put a credential
  in a plugin request.
- **Request body.** Send it as text; the host sends its UTF-8 bytes. A body on
  GET or HEAD is refused. Bodies are capped at 8 MiB in each direction.
- **Errors.** Errors raise `PluginError` with the host's `error_code` as
  `code`, e.g. `origin_blocked`, `timeout` or `too_large`. The message never
  contains the URL.

## Propose holdings and values

With `propose:holdings` and/or `propose:valuations` (Verified tier; each takes
a required `scope.kinds` list of asset kinds from the closed set
`stock_ticker`, `crypto_ticker`, `crypto_exchange`, `home`, `car`,
`precious_metal`, `domain`, `loan_mortgage`) a plugin can **suggest** holdings
and dated values. Nothing is written until the person approves each suggestion
in the app, and the plugin never learns an item id, a current value or an
approval decision.

```python
from datetime import datetime, timezone
from decimal import Decimal
from hellohq_plugin_sdk import host, Holding, Money, Quantity, Source, ProposePermissionDenied, ProposeUnsupported

now = datetime.now(timezone.utc)
batch = [
    Holding(
        source_key="btc:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
        asset_kind="crypto_ticker",
        display_name="Cold wallet",
        quantity=Quantity(Decimal("0.5123"), "BTC"),
        value=Money(Decimal("31744.12"), "USD"),
        as_of=now,
        source=Source("mempool.space", "/api/address/bc1qxy.../utxo", now),
    ),
]
try:
    for receipt in host.propose(batch):          # one Receipt per proposal, in order
        index, outcome, reason = receipt          # e.g. (0, Outcome.QUEUED, None)
except (ProposeUnsupported, ProposePermissionDenied):
    ...  # degrade: return the proposals as data instead
```

- **Receipts.** `host.propose(batch)` returns `Receipt(index, outcome, reason)`
  per proposal. `outcome` is `queued`, `duplicate`, `superseded_older`,
  `unchanged`, `suppressed` or `invalid` (`reason` is then a host reason code
  such as `bad_value` or `fetched_at_outside_run`). A newer outcome this SDK
  does not know arrives as `Outcome.UNKNOWN`: the proposal *was* processed.
- **Input.** A `ProposalBatch`, a list of `Holding` / `Valuation`, plain
  wire-format `dict`s, or a whole `{"schema": "hellohq.proposal-batch@1",
  "proposals": [...]}` mapping: a mapping is sent as given.
- **Money is decimal text.** Amounts are `str`, `int` or `Decimal`
  (`float` raises `TypeError`) and go on the wire as canonical decimal
  strings (`Decimal("0.51230000")` -> `"0.5123"`). Datetimes must be
  timezone-aware and go out as RFC 3339 UTC (`...Z`).
- **Refusals** raise a `ProposeError` (a `PluginError`) with the host's
  `code` and, for `bad_request`, a `reason`: `ProposePermissionDenied`,
  `ProposeUnsupported` (`unknown_method`), `ProposeRateLimited` (10 calls a
  minute and 100 a day per plugin per workspace; `retryable`),
  `ProposeQuotaExceeded` (500 suggestions awaiting review),
  `ProposeTooLarge` (256 KiB), `ProposeTooMany` (200 proposals, 50 holdings),
  `ProposeBadRequest`, `ProposeWorkspaceUnavailable`, `ProposeHostError`.
  Nothing is queued when a call is refused.
- **The host is the authority.** It validates and stamps every batch itself and
  refuses a batch that carries any host-owned field (`plugin_id`, `run_id`,
  `dedup_key`, ...). `hellohq_plugin_sdk.proposal_validation.validate_batch`
  pre-checks the same rules client-side with the host's reason codes so a unit
  test can catch a date-only `as_of` or an unknown field; it can still disagree
  with the host (it does not know the host's currency list, nor whether you
  fetched `source.origin` this run) and never replaces its answer.
- **Older hosts.** A Tier 1 host that predates `propose` does not recognise the
  message and never answers, so the call blocks until the host's own run
  timeout. There is no handshake to probe with: put a `min_host_version` that
  has propose-only writes in your manifest. A host that does answer
  `unknown_method` raises `ProposeUnsupported`.
- `hqplugin test --sidecar` answers `propose` with the mock host, so a
  proposing plugin can be exercised without the app (grant it with
  `--grant propose:holdings=crypto_ticker`).

## Test locally

```bash
echo '{"id":1,"function":"double","args":[21]}' | python your_plugin.py
# -> {"type":"ready",...}
#    {"id":1,"result":{"value":42}}
```

Protocol: https://github.com/HelloHQ/plugin-protocol
