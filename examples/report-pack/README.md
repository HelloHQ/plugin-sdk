# Report Pack — family-meeting report (English and Simplified Chinese)

A Tier-1 Python sidecar with a small WebView UI that turns what HelloHQ lets a
plugin read into a **family-meeting report**, and lets you save it as a
Markdown, plain-text or HTML file. The report is available in English (`en`)
and Simplified Chinese (`zh-Hans`).

This is the first plugin of the HelloHQ plugin roadmap (wave 1). It is
**information, never advice**: it shows what is recorded and says so; it never
recommends a transaction or an action.

> **Status:** example only. It cannot run end to end in the app yet — see
> [Known gaps](#known-gaps) 1 — and plugins are held back in production.

| Half | Lives in | Role |
|------|----------|------|
| **Compute** | `plugin.py` | Tier-1 Python sidecar (`hellohq-plugin-sdk`). Builds the report model and renders it in both languages and three formats. Pure functions, stdlib only. |
| **UI** | `ui/` | Static WebView bundle (vanilla TypeScript). Previews the text report and saves the chosen format through the OS save dialog. |

## What the report contains

1. **Summary** — portfolio counts, the number of currencies, and the combined
   **total assets per currency** of the portfolios whose amounts are shown.
2. **Total assets by portfolio** — one row per portfolio and currency, or one
   row saying why a portfolio's amount is not shown.
3. **Currency exposure** — which currencies the recorded values are in and
   which portfolios have values in each (no amounts, no percentages).
4. **Notes and disclaimer** — what the figures are and are not, what "read at"
   means, rounding, anything missing, withheld or skipped, and the disclaimer.

### Why some amounts are withheld

The host's `read:aggregated_values` gives one number per portfolio and
currency: the sum of the latest recorded value of **every** item in the
portfolio (`PluginDataAccessObject.readAggregatedValues` in the app).
Liabilities are stored as positive amounts and the app subtracts them for net
worth (`overview_networth_total_value.dart`), but the plugin total adds them
in. A portfolio holding a home and its mortgage would show home + mortgage — a
figure that overstates wealth.

So the report asks `read:asset_count` how many asset and liability items each
portfolio has, and:

| Portfolio | Shown as |
|---|---|
| No liability items | its totals, labelled **total assets** (总资产) |
| One or more liability items | "Not shown (includes liabilities)" — amount withheld |
| No item counts available | "Not shown (assets and liabilities unknown)" — amount withheld |
| Totals entry with no values | "No recorded values" |
| No totals entry | "Not available" |

Withheld amounts never enter the model, the documents or the reply to the UI.
Net worth (净资产) is never shown; the notes say so.

### Rules the pack follows (roadmap §5.4)

- **Information only.** No recommendation language anywhere; a plain
  disclaimer closes every report. Tests fail if recommendation words appear in
  any fixed string or rendered document of either language.
- **Provenance on every figure.** Each figure shows its portfolio, its currency
  and the time the workspace was read. Combined figures list the portfolios
  they sum.
- **Currency is always explicit.** Amounts in different currencies are never
  added together or converted, so there is no cross-currency grand total and no
  percentage split between currencies. Currency rates are never read.
- **Honest totals.** Nothing is estimated. Missing, withheld and unreadable
  amounts say so. Negative totals are shown as recorded. Unreadable rows are
  dropped and counted in a note.
- **No network, no AI, no storage.**

### Currencies

The host's `currency_id` is the workspace currency **row id**, not a code: the
nine built-in currencies use fixed UUIDs (`uuid_for_currency.dart`),
import-created rows `document-import-currency-<CODE>`, user-created rows any
id. `plugin.py` resolves a code the way the app does
(`currency_code_helper.dart`): built-in id, then the workspace currency row's
code-shaped name or symbol (from `read:currency_rates`), then the import
prefix, then a bare three-letter id (mock-host). An amount whose currency
cannot be identified is not shown, and a note counts it.

### Terminology (Simplified Chinese)

总资产 (total assets), 负债 (liabilities), 净资产 (net worth — named only to say
it is not shown), 币种 (currency), 读取时间 (read at), 投资组合 (portfolio — the
term the app's own Chinese UI uses). A test checks the terms appear and that
variants (货币, 币别, 资产净值, 总负债, 债务, 截至) do not. The string table
lives in `plugin.py` (`STRINGS`); a test keeps the two languages in
key-and-placeholder lockstep. Traditional Chinese (`zh-Hant`, `zh-TW`, `zh-HK`)
is rejected with `invalid_input` rather than silently served as Simplified;
bare `zh` means Simplified.

## Permissions and why

Declared in `manifest.json`, each with a `reason` shown at install review:

| Permission | Tier | Why |
|---|---|---|
| `read:portfolio_names` | Community | Portfolio names for the report. |
| `read:asset_count` | Community | Asset vs liability item counts, so a total that mixes them is not shown as assets. |
| `read:currency_rates` | Community | The workspace currency list, to show each total's currency code. The rates are never read; nothing is converted. |
| `read:aggregated_values` | **Verified** | The per-portfolio, per-currency totals. |
| `write:external_output` | **Verified** | The UI saves the report through the OS save dialog; the plugin never learns the path. |

`execution_mode: sidecar` and `ui_type: webview` are Verified-only too, so the
plugin needs the **Verified** tier. `trust_tier` is not in the manifest: the
registry team sets it at merge (docs/plugin/03). Deliberately **not**
requested: `network:fetch`, `ai:inference`, `plugin:storage`,
`read:sheet_structure`. The plugin sees no individual item, balance history or
account identifier. Tests pin the permission list, check every context key
`plugin.py` reads is declared, and check `plugin.py` imports nothing that can
reach a network or the AI backend.

## Why a Tier-1 Python sidecar?

The choice is driven by `write:external_output`, not taste:

- `write:external_output` is wired **only for WebView UIs** (the host's
  `write_external` bridge action). The Tier-2 WIT world reserves
  `write-external-file` with "no Tier-2 wiring yet", and the Python sidecar SDK
  has no write call. So a Tier-2 Rust/Go/JS component could not deliver the
  file at all today, whatever its language.
- The sidecar receives the host's pre-fetched, permission-gated context in
  `args["context"]`, the CLI can run it (`hqplugin test --sidecar`), and the
  Python SDK has its own test suite.
- The report is string and decimal work — Python's `decimal` and `unicodedata`
  fit it, with no dependency beyond the SDK.

The save is therefore a UI action (`HQHost.writeExternal`), and the sidecar
returns the three rendered documents.

## How the two halves talk

```ts
const host = new HQHost();
const report = await host.compute("report", { lang: "zh-Hans" });
// report.documents.markdown | .text | .html  ->  { filename, mime, content }
await host.writeExternal(report.documents.html.filename, report.documents.html.content);
```

`HQHost` calls the app's injected `window.HQBridge` shim
(`plugin_webview_init_script.dart`): `compute(fn, args)` becomes
`{action: "compute", payload: {function, args}}` and
`writeExternal(name, text)` becomes
`{action: "write_external", payload: {suggested_filename, content_base64}}`
(UTF-8, no BOM). The host calls the sidecar's `run` with
`{"context": {...}, "input": {"function": "report", "args": {"lang": "…"}}}`.
`context` holds the pre-fetched reads keyed by permission id; a denied read is
simply absent and the report says so. The shapes (`PluginDataAccessObject`):

```text
"read:portfolio_names":   [{"id": "...", "name": "..."}]
"read:asset_count":       {"portfolios": [{"id": "...", "asset_items": 3, "debt_items": 1, "total_items": 4}]}
"read:currency_rates":    [{"id": "<row id>", "name": "USD", "symbol": "$", "rate": 1000000}]
"read:aggregated_values": {"portfolios": [{"id": "...", "totals": [{"currency_id": "<row id>", "total": 1.5}]}]}
```

`compute` args must be strings, numbers, booleans or flat arrays of them (the
app's bridge rejects nested objects and `null`); the only argument is the
language string. The preview uses `textContent`, never `innerHTML`.

### Inside `plugin.py`

1. `build_report(context, now)` → a language-neutral **model** (amounts as exact
   decimal strings, notes as codes). Amounts come from `Decimal(repr(float))`,
   so no binary-float noise reaches a figure; sums are exact (80-digit context).
2. `build_blocks(model, lang)` → a small document IR (headings, paragraphs,
   lists, tables) using the string table.
3. `render_markdown` / `render_text` / `render_html` → the saved files.

Rendering safety: portfolio names are user data. Control characters and
bidirectional overrides are stripped; Markdown specials are escaped (so a name
cannot add table columns, a link or an autolink); HTML is escaped, carries a
`default-src 'none'` CSP, loads nothing, and declares `lang`. Names longer than
80 characters are shortened with an ellipsis in the documents (the model keeps
the full name, and a note says so). Amounts are rounded half to even to each
currency's usual decimals (0 for JPY/KRW, 3 for KWD, …), exact up to 10^31;
combined totals are summed before rounding.

## Build

```bash
./build.sh
```

Checks `plugin.py` parses (it is the sidecar artifact itself, a single file
importing only the stdlib and `hellohq_plugin_sdk`), builds the in-repo JS SDK
(`sdks/js/dist` is not committed), type-checks and bundles the UI with esbuild,
zips `ui/dist` into a reproducible `ui.zip` (fixed order and timestamps), and
prints both sha256 hashes to paste into `manifest.json` at release time
(committed hashes are placeholders).

Requirements: Node 20+ and npm, Python 3.11+, `zip`.

## Test

```bash
cd examples/report-pack
./build.sh                                  # so tests/test_build.py can check ui.zip
uv run --no-project --with pytest --with pytest-cov pytest --cov=plugin --cov-report=term-missing
uvx ruff@0.16.8 check plugin.py tests && uvx ruff@0.16.8 format --check plugin.py tests
(cd ../../sdks/js && npm test)              # HQHost against the app's HQBridge shim
```

CI runs all of this in the `Report Pack example` job, plus the sidecar through
`hqplugin test --sidecar` on Linux, macOS and Windows.

- `tests/test_report.py` — model, both languages, three renderers; golden
  outputs in `tests/golden/` for eight scenarios (a family with a mortgaged
  home, all debt-free, all with liabilities, zero portfolios, missing totals,
  partial totals, negative totals, hostile names) and edge cases: currency id
  resolution (built-in UUIDs, user rows, import rows, unknown ids), withheld
  amounts never leaking, malformed counts, many currencies, 0- and 3-decimal
  currencies, half-even rounding, 10^30-sized sums, the double-precision note,
  NaN/inf/bool/garbage rows, duplicate ids, language selection, and names that
  are very long, empty, multi-line, bidi-spoofed or full of Markdown/HTML.
  Regenerate goldens after an intentional wording change with
  `UPDATE_GOLDEN=1 pytest` and review the diff.
- `tests/test_sidecar_e2e.py` — runs `plugin.py` as the real sidecar process
  over the NDJSON protocol the host speaks (ready → `run` RPC → shutdown), with
  host-shaped context, in both languages, plus withheld-amount, denied-context,
  repeat-call and error-envelope cases.
- `tests/test_manifest.py` — permission set and reasons, Verified tier, manifest
  shape vs. the sibling example, and the no-network / no-AI import guard.
- `tests/test_build.py` — the built `ui.zip`: expected files, entrypoint,
  reproducible timestamps, nothing the host CSP blocks (no inline script, no
  remote URL, no `eval`/`fetch`), and the `write_external` contract in the
  bundle. Skipped when `ui.zip` is absent unless `REPORT_PACK_REQUIRE_BUILD=1`.
- `sdks/js/test/hqhost.test.mjs` — `HQHost` (including `writeExternal`) driven
  through a verbatim copy of the app's HQBridge shim and a fake host that
  validates like `PluginWebViewBridge`.

## Known gaps

Read these before relying on the report.

1. **The app cannot run this plugin end to end yet.** The WebView pane sends
   every `compute` to the Tier-2 (Wasm component) executor
   (`plugin_webview_host.dart` `_buildBridge` uses
   `pluginTier2ExecutorProvider`), so a sidecar plugin's `compute` fails there.
   docs/plugin/07 says the host routes `compute` by the installed binary; the
   code does not. Every sidecar + WebView example (`portfolio_summary`,
   `fx-advisor`, `portfolio-analyst`, `notes-keeper`) has the same problem. Fix
   in hellohq: route WebView `compute` to `pluginTier1ExecutorProvider` for
   `execution_mode: sidecar`.
2. **Totals mix assets and liabilities.** See
   [Why some amounts are withheld](#why-some-amounts-are-withheld). Many
   families hold a mortgage, so many reports will show few amounts. Fix in the
   host: return totals split by sheet type; the report could then show total
   assets, liabilities and net worth.
3. **The plugin total can differ from the app's, even for a debt-free
   portfolio.** The host sums every value-history row of the portfolio, while
   the app's own totals leave out legacy `crypto` items and items suppressed
   by import or account-merge review (`sync_conflict_suppressed`,
   `bank_merge_suppressed`), and count only items under a sheet and section.
   So a merged bank account can be counted twice. The report says figures may
   differ from the app. Fix in the host (inferred from the app code, not
   reproduced against a real workspace).
4. **"Read at" is the read time.** The host does not expose a date for the
   values inside a total, so each figure carries the moment the report read
   the workspace (UTC), and the report says a total can include values
   recorded long before. A report cannot honestly say "as at 31 March".
5. **Totals cross the host boundary as doubles.** The host sums exactly, then
   converts to a JSON number. At or above 2^53 minor units (about 90 trillion
   for a two-decimal currency) the last digits may be inexact; the report adds
   a note when an amount is that large.
6. **Currencies.** The built-in currency ids are copied from the app. A
   user-created currency whose name and symbol are not code-shaped (2–8 Latin
   letters) cannot be identified and is not shown. Codes the app treats as
   currencies but ISO does not (e.g. `POUND` from a name "Pound") are shown as
   is, with two decimals.
7. **Portfolio scope.** The manifest reference says `read:aggregated_values`
   needs a `scope.portfolios` list and that registry CI rejects an entry
   without one, but every shipped example (and the host's sidecar snapshot,
   which reads all portfolios) uses it unscoped, as this plugin does. A report
   pack cannot know the person's portfolio ids in advance; the registry or the
   host will need a "chosen at install" scope.
8. **No production use until signing ships.** Plugins are held back in
   production: publisher signing (SA5) is dormant, `hqplugin publish` is not
   implemented, and the committed hashes are zeros. `trust_tier: verified` must
   be set by the registry team.
9. **Local CLI cannot feed context.** `hqplugin test --sidecar` calls `run`
   with empty args — `MockSidecarHost` does not build the `context` snapshot,
   and `--bundle` cannot load the UI. The CLI run (also in CI) therefore shows
   the no-portfolios report; the pytest e2e covers real context.
10. **No automated DOM test of the UI.** The UI is type-checked and its bundle
    is checked by `tests/test_build.py`; `HQHost` is tested against the app's
    shim. The page itself was exercised by hand in a browser: the app's shim
    plus a fake host answering `compute` with real `plugin.py` output, then a
    language switch and an HTML save checked byte for byte.
11. **Chinese has not had a native review.** The copy was written for natural
    financial Simplified Chinese but has not been reviewed by a native finance
    reader. In particular: the app's own UI says 债务 for the debt page and this
    report says 负债; "read at" is 读取时间; half-even rounding is described as
    四舍六入五成双.
12. **`HQHost` read types do not match the host.** `readAggregatedValues`,
    `readAssetCount` and `readSheetStructure` are typed with shapes the app does
    not return (it returns the `{"portfolios": [...]}` shapes above). This
    plugin does not use them; fixing the types is separate SDK work.

## License

Apache 2.0.
