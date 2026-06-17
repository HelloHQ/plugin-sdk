# Notes Keeper — Tier-1 Python sidecar with a WebView UI

A plugin that persists a run counter and a free-text note via `plugin:storage`.
The example proves that a **Tier-1 Python sidecar can drive a WebView UI** — the
same model as the Tier-2 Rust examples, with the host routing `compute` to the
sidecar instead of a Wasm component.

| Half | Lives in | Role |
|------|----------|------|
| **Compute** | `plugin.py` | A Tier-1 Python sidecar (`hellohq-plugin-sdk`). Reads/writes storage, returns JSON, emits a push event. |
| **UI** | `ui/` | A static WebView bundle (vanilla TypeScript). Talks to the sidecar via `window.HQBridge`. |

## How the two halves talk

The UI uses `@hellohq/plugin-sdk`'s `HQHost`; the host routes `compute` to the
Python sidecar (Tier-1) exactly as it routes to a Wasm component (Tier-2):

```ts
const host = new HQHost();
const state = await host.compute("run", {});            // -> {count, note}
await host.compute("save_note", { note });              // persists
host.on("note-saved", () => { /* sidecar emit_event */ });
```

- `host.compute(fn, args)` runs `dispatch(fn, args)` in `plugin.py` and resolves
  with the JSON it returns.
- `host.on("note-saved", cb)` receives the event the sidecar pushes with
  `emit_event(...)` — Tier-1 sidecars get the same push path as Tier-2 plugins.

This is framework-agnostic: the contract is `window.HQBridge` / `HQHost`, not the
UI framework. Other examples build the same UX in Svelte / React / Vue.

> WebView mode requires the Verified trust tier and desktop, and the sidecar is
> network-jailed (it has no sockets of its own). Storage stays a per-plugin,
> sandboxed key-value store.

## Build

```bash
./build.sh
```

The Python sidecar is `plugin.py` itself (no compile step); `build.sh`
byte-compiles it as a sanity check, bundles the UI, zips `ui/dist` into
`ui.zip`, and prints both sha256 hashes to paste into `manifest.json` at release
time (committed hashes are placeholders).

Requirements: Node + npm; Python 3.

## Test locally

```bash
hqplugin test --sidecar plugin.py --bundle ui.zip
```
