# Vendored WIT — `hellohq:plugin@0.1.0`

`hellohq-plugin.wit` is **vendored** from the canonical source of truth:

> `HelloHQ/plugin-protocol` → `wit/hellohq-plugin.wit`

It is copied here because this SDK is a separate repository from
`plugin-protocol`. Do not hand-edit it — edit the SSOT and re-sync.

## Re-sync

```sh
../scripts/sync-wit.sh            # copies the SSOT over the vendored copy
```

(Assumes `plugin-protocol` is checked out next to `plugin-sdk`; override with
`PLUGIN_PROTOCOL=/path/to/plugin-protocol ../scripts/sync-wit.sh`.)

After syncing, validate:

```sh
wasm-tools component wit wit/
```

## Files

- **`hellohq-plugin.wit`** — the canonical package + the full `hellohq-plugin`
  world (imports `types`/`workspace`/`storage`/`events`/`log`/`inference`,
  exports `guest`). This is what plugins normally build against; `wasm-tools
  component new` tree-shakes away any interface the plugin never calls.
- **`quickstart.wit`** — SDK-local supplementary worlds in the same
  `hellohq:plugin` package. `inference-quickstart` exports an **`async` `run`**
  so a plugin can drain the streaming `inference.complete` result (the canonical
  `guest.run` is sync and cannot await). Not part of the SSOT.

## Follow-up

A git submodule pointing at `HelloHQ/plugin-protocol` would be cleaner than a
vendored copy long-term (single source, no drift). Deferred — not set up here.
