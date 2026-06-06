# hqplugin CLI

Build, test, and publish HelloHQ plugins.

```bash
dart pub get
dart run hqplugin --help

hqplugin build --lang rust --entry src/lib.rs --out plugin.wasm
hqplugin test  --wasm plugin.wasm            # runs against the mock host
hqplugin publish --version 1.0.0             # opens a registry PR
```

> **Status:** command surface scaffold (Phase 4 — begin). Commands parse and
> print their plan; build/test/publish implementations land incrementally.
> Distributed via pub.dev and Homebrew when complete.
