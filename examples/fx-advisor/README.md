# FX Opportunity Advisor — Tier-1 Python sidecar with a Vue WebView UI

A plugin that reads the workspace's current currency rates and runs a single
`ai:inference` call to produce a **structured** FX opportunity analysis: which
currencies look strong vs weak against USD, plus a short portfolio implication.
The example proves that a **Tier-1 Python sidecar can drive a Vue WebView UI** —
the same model as the Tier-2 examples, with the host routing `compute` to the
sidecar instead of a Wasm component.

| Half | Lives in | Role |
|------|----------|------|
| **Compute** | `plugin.py` | A Tier-1 Python sidecar (`hellohq-plugin-sdk`). Summarises the prefetched currency rates, asks `host.ai_complete` for STRICT-JSON, parses it (degrades to raw text on failure), returns JSON, emits a progress event. |
| **UI** | `ui/` | A Vue 3 + Vite WebView bundle. Triggers the analysis and renders the headline, strong/weak currency lists, recommendation, and a model/token footer. |

## How the two halves talk

The UI uses `@hellohq/plugin-sdk`'s `HQHost`; the host routes `compute` to the
Python sidecar (Tier-1) exactly as it routes to a Wasm component (Tier-2):

```ts
const host = new HQHost();
const result = await host.compute("analyse", {});
// -> { headline, strong, weak, recommendation, model, tokens }
```

- `host.compute("analyse", {})` runs `dispatch("run", …)` in `plugin.py`. The
  host wraps the call as `{"context": {<perm-id>: <read>, …}, "input":
  {"function": "analyse", "args": {…}}}`, so the sidecar routes on
  `args["input"]["function"]` and reads the prefetched, permission-gated rates
  from `args["context"]["read:currency_rates"]`.

### The analyse flow

1. UI calls `host.compute("analyse", {})`.
2. Sidecar builds a compact rates summary from `read:currency_rates` (treated as
   opaque JSON; USD base excluded; absent/denied reads degrade gracefully).
3. Sidecar calls `host.ai_complete` with a system prompt instructing the model
   to respond as STRICT JSON `{"headline","strong","weak","recommendation"}`,
   where `strong`/`weak` are currency ids vs USD.
4. Sidecar parses the JSON with `json.loads`. On parse failure it degrades to
   `{"headline": "…", "strong": [], "weak": [], "recommendation": <raw text>}`.
5. Sidecar returns the analysis plus `{model, tokens}`; the UI renders the
   headline, the strong/weak lists, the recommendation, and a model/token footer.

A token-budget error is caught and returned as a friendly message rather than a
hard failure. Denied reads are simply absent from `args["context"]`, so the
plugin degrades gracefully. A `HQPermissionError` from the bridge surfaces as an
error state in the UI.

> WebView mode requires the Verified trust tier and desktop, and the sidecar is
> network-jailed (it has no sockets of its own). AI inference is routed through
> the user's configured BYOK backend — the plugin never sees the API key.

## Build

```bash
./build.sh
```

The Python sidecar is `plugin.py` itself (no compile step); `build.sh`
byte-compiles it as a sanity check, builds the Vue UI with Vite, zips `ui/dist`
into `ui.zip`, and prints both sha256 hashes to paste into `manifest.json` at
release time (committed hashes are placeholders).

Requirements: Node + npm; Python 3.

## Test locally

```bash
hqplugin test --sidecar plugin.py --bundle ui.zip
```
