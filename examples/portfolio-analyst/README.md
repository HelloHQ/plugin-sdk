# Portfolio AI Analyst — Tier-1 Python sidecar with a React WebView UI

A plugin that reads the user's aggregated portfolio data and runs a short,
two-step `ai:inference` reasoning loop to produce a plain-language analysis. The
example proves that a **Tier-1 Python sidecar can drive a React WebView UI** —
the same model as the Tier-2 Rust examples, with the host routing `compute` to
the sidecar instead of a Wasm component.

| Half | Lives in | Role |
|------|----------|------|
| **Compute** | `plugin.py` | A Tier-1 Python sidecar (`hellohq-plugin-sdk`). Summarises the prefetched portfolio context, runs a 2-step `host.ai_complete` loop, returns JSON, emits progress events. |
| **UI** | `ui/` | A React + Vite WebView bundle. Triggers the analysis and renders it, subscribing to progress events. |

## How the two halves talk

The UI uses `@hellohq/plugin-sdk`'s `HQHost`; the host routes `compute` to the
Python sidecar (Tier-1) exactly as it routes to a Wasm component (Tier-2):

```ts
const host = new HQHost();
host.on("analysis-progress", (p) => { /* show step progress */ });
const result = await host.compute("analyse", {});  // -> {observations, recommendations, model, tokens}
```

- `host.compute("analyse", {})` runs `dispatch("run", …)` in `plugin.py`. The
  host wraps the call as `{"context": {<perm-id>: <read>, …}, "input":
  {"function": "analyse", "args": {…}}}`, so the sidecar routes on
  `args["input"]["function"]` and reads the prefetched, permission-gated data
  from `args["context"]`.
- `host.on("analysis-progress", cb)` receives the events the sidecar pushes with
  `emit_event(...)` between reasoning steps — Tier-1 sidecars get the same push
  path as Tier-2 plugins.

### The analyse + progress-event flow

1. UI calls `host.compute("analyse", {})`.
2. Sidecar builds a compact portfolio context from `read:portfolio_names` +
   `read:aggregated_values` (names and aggregates only — never raw identifiers).
3. **Step 1** emits `analysis-progress {step: "observations"}`, then asks the AI
   for key observations.
4. **Step 2** emits `analysis-progress {step: "recommendations"}`, then turns
   those observations into plain-language suggestions.
5. Sidecar returns `{observations, recommendations, model, tokens}`; the UI
   renders both sections plus a model/token footer.

A token-budget error is caught and returned as a friendly message rather than a
hard failure. Denied reads are simply absent from `args["context"]`, so the
plugin degrades gracefully.

> WebView mode requires the Verified trust tier and desktop, and the sidecar is
> network-jailed (it has no sockets of its own). AI inference is routed through
> the user's configured BYOK backend — the plugin never sees the API key.

## Build

```bash
./build.sh
```

The Python sidecar is `plugin.py` itself (no compile step); `build.sh`
byte-compiles it as a sanity check, builds the React UI with Vite, zips
`ui/dist` into `ui.zip`, and prints both sha256 hashes to paste into
`manifest.json` at release time (committed hashes are placeholders).

Requirements: Node + npm; Python 3.

## Test locally

```bash
hqplugin test --sidecar plugin.py --bundle ui.zip
```
