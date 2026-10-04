# Report Pack — family-meeting report (English and Simplified Chinese)

A Tier-1 Python sidecar with a small WebView UI that turns your portfolio names
and per-portfolio totals into a **family-meeting report** and lets you save it
as a Markdown, plain-text or HTML file. The report is available in English
(`en`) and Simplified Chinese (`zh-Hans`).

This is the first plugin of the HelloHQ plugin roadmap (wave 1). It is
**information, never advice**: it shows what is recorded and says so; it never
recommends a transaction or an action.

| Half | Lives in | Role |
|------|----------|------|
| **Compute** | `plugin.py` | Tier-1 Python sidecar (`hellohq-plugin-sdk`). Builds the report model and renders it in both languages and three formats. Pure functions, stdlib only. |
| **UI** | `ui/` | Static WebView bundle (vanilla TypeScript). Previews the text report and saves the chosen format through the OS save dialog. |

## What the report contains

1. **Summary** — portfolio counts, the number of currencies, and the combined
   recorded total **per currency**.
2. **Totals by portfolio** — one row per portfolio and currency.
3. **Currency exposure** — which currencies appear and which portfolios hold
   them.
4. **Notes and disclaimer** — what the totals are (and are not), what "as of"
   means, rounding, anything that was missing or skipped, and the disclaimer.

Rules the pack follows (roadmap §5.4):

- **Information only.** No recommendation language anywhere; a plain disclaimer
  closes every report. A test fails if recommendation words appear in the fixed
  copy of either language.
- **Provenance on every figure.** Each figure shows its portfolio, its currency
  and an as-of time. Combined figures list the portfolios they sum.
- **Currency is always explicit.** Amounts in different currencies are never
  added together or converted, so no cross-currency "grand total" and no
  percentage split between currencies is shown.
- **Honest totals.** Nothing is estimated. A portfolio with no total says "Not
  available"; a report with no totals says so and shows no figures. Negative
  totals are shown as recorded. Unreadable rows are dropped and counted in a
  note.
- **No network, no AI.**

### Terminology (Simplified Chinese)

净资产 (net worth), 总资产 (total assets), 负债 (liabilities), 币种 (currency),
截至 (as of), 投资组合 (portfolio — the term the app's own Chinese UI uses). A
test checks the terms appear and that variants (货币, 币别, 资产净值, 债务, …)
do not. The string table lives in `plugin.py` (`STRINGS`); a test keeps the two
languages in key-and-placeholder lockstep. Traditional Chinese (`zh-Hant`,
`zh-TW`, `zh-HK`) is rejected with `invalid_input` rather than silently served
as Simplified.

## Permissions and why

Declared in `manifest.json`, the same way as the other examples:

| Permission | Tier | Why |
|---|---|---|
| `read:portfolio_names` | Community | Portfolio names for the report. |
| `read:aggregated_values` | **Verified** | The per-portfolio, per-currency totals. |
| `write:external_output` | **Verified** | The UI saves the report through the OS save dialog; the plugin never learns the path. |

Deliberately **not** requested: `network:fetch`, `ai:inference`,
`plugin:storage`, `read:currency_rates` (it would only invite conversions the
report refuses to make), `read:sheet_structure`, `read:asset_count`. The
plugin sees no individual item, balance history or account identifier.
A test pins the permission list and checks `plugin.py` imports nothing that can
reach a network or the AI backend.

## Why a Tier-1 Python sidecar?

The choice is driven by `write:external_output`, not taste:

- `write:external_output` is wired **only for WebView UIs** (the host's
  `write_external` bridge action). The Tier-2 WIT world reserves
  `write-external-file` with "no Tier-2 wiring yet", and the Python sidecar SDK
  has no write call. So a Tier-2 Rust/Go/JS component could not deliver the
  file at all today, whatever its language.
- The best-maintained examples with a WebView UI are the Tier-1 sidecar ones
  (`portfolio_summary`, `portfolio-analyst`, `fx-advisor`): they are the ones the
  CLI can run (`hqplugin test --sidecar`), the Python SDK has its own test
  suite, and the sidecar receives the host's pre-fetched, permission-gated
  context in `args["context"]`.
- The report is string and decimal work — Python's `decimal` and `unicodedata`
  fit it, with no dependency beyond the SDK.

The save is therefore a UI action (`HQHost.writeExternal`, added to the JS SDK
for this plugin — see Known gaps), and the sidecar returns the three rendered
documents.

## How the two halves talk

```ts
const host = new HQHost();
const report = await host.compute("report", { lang: "zh-Hans" });
// report.documents.markdown | .text | .html  ->  { filename, mime, content }
await host.writeExternal(report.documents.html.filename, report.documents.html.content);
```

The host calls the sidecar's `run` with
`{"context": {...}, "input": {"function": "report", "args": {"lang": "…"}}}`.
`context` holds the pre-fetched reads keyed by permission id; a denied read is
simply absent and the report says so. The shapes the sidecar reads (matching
`PluginDataAccessObject` in the app and `mock-host`):

```text
"read:portfolio_names":   [{"id": "...", "name": "..."}]
"read:aggregated_values": {"portfolios": [{"id": "...",
                          "totals": [{"currency_id": "usd", "total": 1.5}]}]}
```

`compute` args must be JSON primitives (the host bridge rejects nested
objects); the only argument is the language string. The preview uses
`textContent`, never `innerHTML`.

### Inside `plugin.py`

1. `build_report(context, now)` → a language-neutral **model** (amounts as exact
   decimal strings, notes as codes). Amounts come from `Decimal(repr(float))`,
   so no binary-float noise reaches a figure.
2. `build_blocks(model, lang)` → a small document IR (headings, paragraphs,
   lists, tables) using the string table.
3. `render_markdown` / `render_text` / `render_html` → the saved files.

Rendering safety: portfolio names are user data. Control characters and
bidirectional overrides are stripped; Markdown specials are escaped (so a name
cannot add table columns, a link or an autolink); HTML is escaped, carries a
`default-src 'none'` CSP, loads nothing, and declares `lang`. Names longer than
80 characters are shortened with an ellipsis in the documents (the model keeps
the full name, and a note says so). Amounts are rounded half-even to each
currency's usual decimals (0 for JPY/KRW, 3 for KWD, …), exact up to 10^30.

## Build

```bash
./build.sh
```

Byte-compiles `plugin.py` (the sidecar artifact itself, so it stays a single
file importing only the stdlib and `hellohq_plugin_sdk`), builds the in-repo JS
SDK (`sdks/js/dist` is not committed), type-checks and bundles the UI with
esbuild, zips `ui/dist` into `ui.zip`, and prints both sha256 hashes to paste
into `manifest.json` at release time (committed hashes are placeholders).

Requirements: Node + npm, Python 3.11+.

## Test

```bash
cd examples/report-pack
uv run --with pytest --with pytest-cov pytest --cov=plugin --cov-report=term-missing
# or, with pytest already installed:  python3 -m pytest

ruff check plugin.py tests && ruff format --check plugin.py tests   # lint
(cd ui && npm run typecheck)                                         # UI types
```

`tests/conftest.py` puts the in-repo `sdks/python` on `sys.path` (as
`hqplugin test --sidecar` does), so no pip install is needed.

- `tests/test_report.py` — model, both languages, three renderers; golden
  outputs in `tests/golden/` for six scenarios (typical family, zero
  portfolios, missing totals, partial totals, negative totals, hostile names)
  and edge cases: mixed currencies, negative and near-zero amounts, 0- and
  3-decimal currencies, 10^30-sized sums, NaN/inf/bool/garbage rows, duplicate
  ids, names that are very long, empty, multi-line, bidi-spoofed or full of
  Markdown/HTML. Regenerate goldens after an intentional wording change with
  `UPDATE_GOLDEN=1 pytest` and review the diff.
- `tests/test_sidecar_e2e.py` — runs `plugin.py` as the real sidecar process
  over the NDJSON protocol the host speaks (ready → `run` RPC → shutdown), with
  host-shaped context, in both languages, plus denied-context, repeat-call and
  error-envelope cases.
- `tests/test_manifest.py` — permission set, manifest shape vs. the sibling
  example, and the no-network / no-AI import guard.

`hqplugin test --sidecar plugin.py` also runs it (see Known gaps for what the
CLI cannot yet feed it):

```bash
cd cli && dart run bin/hqplugin.dart test --sidecar ../examples/report-pack/plugin.py
```

## Known gaps

Read these before relying on the report:

1. **Totals are not net worth.** The host's `read:aggregated_values` returns
   one number per portfolio and currency: the sum of each item's latest
   recorded value (`PluginDataAccessObject.readAggregatedValues`). It does not
   say whether an item is an asset or a debt, and the app's own net-worth code
   suggests debt values are stored as positive amounts and subtracted. So these totals may not
   equal 净资产 / net worth, and 总资产 (total assets) and 负债 (liabilities)
   **cannot be shown separately**. The report therefore labels figures
   "recorded total" / "记录合计" and says so in a note instead of claiming net
   worth. Fixing this needs the host to return asset and debt totals
   separately (e.g. by sheet type); the report could then show total assets,
   liabilities and a true net worth. Not hacked around here.
2. **"As of" is the read time.** The host does not expose a date for the
   values inside a total, so each figure is stamped with the moment the report
   read the workspace (UTC), and the report says that. Because of this a
   report cannot honestly say "net worth as at 31 March".
3. **`write:external_output` is wired only for WebView UIs.** The WIT world
   reserves `write-external-file` without Tier-2 wiring, and the Python sidecar
   SDK has no write call, so saving is a UI action. This plugin therefore
   needs `ui_type: webview`, which is Verified-tier and desktop-only, and has
   no headless/scheduled export. `HQHost` had no method for the bridge's
   `write_external` action; `writeExternal` was added to `sdks/js` in a
   separate commit.
4. **Portfolio scope on `read:aggregated_values`.** The manifest reference says
   the permission needs a `scope.portfolios` list and that registry CI rejects
   an entry without one, but every shipped example (and the host's sidecar
   snapshot, which reads all portfolios) uses it unscoped, as this plugin
   does. A report pack cannot know the person's portfolio ids in advance. If CI
   enforces the scope, the registry or the host will need a "chosen at
   install" scope.
5. **No production use until signing ships.** Plugins are held back in
   production: publisher signing (SA5) is dormant, manifest hashes are
   placeholders and `hqplugin publish` is not implemented. The manifest is
   unsigned and its hashes are zeros.
6. **Local test tooling cannot feed context.** `hqplugin test --sidecar`
   calls `run` with empty args — `MockSidecarHost` does not build the
   `context` snapshot, there is no flag to pass `input`, and `--bundle` ("not
   yet supported") cannot load the UI. The CLI run therefore shows the
   no-portfolios report; the pytest e2e covers real context.
7. **The UI is exercised by type-check and a manual harness only.** There is
   no JS test runner in the repo. The UI was driven once against a fake
   `HQBridge` backed by the real `generate()`; there is no automated UI test.
8. **Not in CI.** `.github/workflows/ci.yml` has no job for example tests;
   the pytest suite above runs locally.
9. **Chinese review.** The copy was written for natural financial Chinese but
   has not been reviewed by a native finance reader. In particular the app's
   own UI says 债务 for the debt page; this report uses 负债 as the plan
   specifies.
10. **Extra currencies.** Currency ids are shown upper-cased as the host sends
    them; non-ISO ids (crypto tickers, if the app records any) are shown as is
    and use two decimals.

## License

Apache 2.0.
