// SPDX-License-Identifier: Apache-2.0
//
// HelloHQ Tier-2 **Component Model** plugin SDK for TypeScript / JavaScript.
//
// Build a HelloHQ plugin as a WebAssembly **component** against the canonical
// `hellohq:plugin@0.1.0` WIT (vendored in `wit/`) using
// `jco` + `componentize-js`. The host implements and permission-gates every
// import.
//
// This is the JS analogue of the Rust SDK (`hellohq-plugin-sdk`): the same
// ergonomic `hq.{workspace,storage,events,log,inference}` surface plus a clear
// way to define the `guest` exports (`init` / `run` / `metadata`).
//
// ── How the imports resolve ──────────────────────────────────────────────────
// The `hellohq:plugin/*@0.1.0` interfaces are *imports* satisfied by the host.
// `jco componentize` resolves the bare specifiers below at build time and wires
// each call straight to the host import. This module is therefore only
// meaningful inside a componentized plugin; importing it in plain Node will
// fail to resolve `hellohq:plugin/*`.
//
// ── Error model ──────────────────────────────────────────────────────────────
// In WIT every gated call returns `result<T, api-error>`. jco maps that to
// "return `T`, or **throw** the `api-error`". So the `hq.*` helpers return the
// success value directly and THROW an {@link ApiError} on a gate denial /
// validation failure / downstream error. You MUST wrap gated calls in try/catch
// and RETURN a degraded value: an *uncaught* throw out of `run` does NOT become
// the WIT `Err(string)` — in the current jco/componentize-js it traps the guest
// (see {@link definePlugin}). This mirrors Rust's `Result<…, ApiError>`, but the
// guest — not an uncaught throw — must convert the error into a return value.
//
// ── Streaming inference ──────────────────────────────────────────────────────
// `hq.inference.complete` is declared against the canonical WIT
// (`-> stream<string>`, surfaced by jco as `ReadableStream<string>`).
//
// The JS-side `stream<T>` RUNTIME already exists: `@bytecodealliance/preview3-shim`
// implements WASI 0.3 streams/futures (`StreamReader`/`StreamWriter`/`stream()`,
// bridged to WHATWG `ReadableStream`). The remaining gap is the GUEST ENGINE:
// the pinned componentize-js (0.21.0) crashes building a component whose world
// imports a `stream` type, and even on newer P3 builds a native JS Promise isn't
// yet driven by the component-model-async executor. That work is in flight
// upstream (jco's `preview3-shim` + p3 bindgen "lift streams as typed arrays";
// `dicej/componentize-js`) but UNRELEASED. So the default build world
// (`hellohq-plugin-component`) OMITS `inference`; this helper is kept for
// forward-compatibility and the raw-bindings escape hatch, and a plugin that
// calls `hq.inference.*` will fail at `jco componentize` until the engine ships
// stream support. See `wit/README.md`.

import { PROTOCOL_VERSION } from "./index.js";

// Re-export the legacy/WebView surface too, so `@hellohq/plugin-sdk` is one
// import for both modes. The component-mode entry point is this module
// (`@hellohq/plugin-sdk/component`).
export { PROTOCOL_VERSION };

// ─────────────────────────────────────────────────────────────────────────────
// Raw host imports (satisfied by the host at component instantiation).
//
// `jco componentize` resolves these bare `hellohq:plugin/*@0.1.0` specifiers.
// They have no runtime meaning outside a componentized plugin. `@ts-ignore` /
// the ambient `.d.ts` in `generated/` provide the types; the `as` casts below
// keep this file type-checkable in a plain `tsc` build (where the virtual
// modules don't exist on disk).
// ─────────────────────────────────────────────────────────────────────────────

import * as rawWorkspace from "hellohq:plugin/workspace@0.1.0";
import * as rawStorage from "hellohq:plugin/storage@0.1.0";
import * as rawEvents from "hellohq:plugin/events@0.1.0";
import * as rawLog from "hellohq:plugin/log@0.1.0";
// NOTE: `inference` is loaded LAZILY (dynamic import inside `inference.complete`)
// on purpose. A static `import` would emit a top-level `hellohq:plugin/inference`
// import into every bundle, and the default build world
// (`hellohq-plugin-component`) deliberately omits `inference` because
// componentize-js 0.21.0 cannot componentize a world carrying a `stream` type
// (see `wit/README.md`). Keeping it lazy means a plugin that never calls
// `hq.inference.*` produces no `inference` import at all.

// ─────────────────────────────────────────────────────────────────────────────
// Re-exported record / error types (generated from the WIT, clean names).
// ─────────────────────────────────────────────────────────────────────────────

import type { ApiError as _ApiError } from "hellohq:plugin/types@0.1.0";
import type {
  PortfolioName,
  SheetSummary,
  SheetInfo,
  AssetCount,
  CategoryCount,
  CurrencyRate,
  AggregatedSummary,
  CategoryTotal,
} from "hellohq:plugin/types@0.1.0";
import type { PluginEvent } from "hellohq:plugin/events@0.1.0";
import type { Level as LogLevel } from "hellohq:plugin/log@0.1.0";
import type { ChatMessage, InferenceOpts } from "hellohq:plugin/inference@0.1.0";
import type { PluginMetadata } from "hellohq:plugin/guest@0.1.0";

export type {
  PortfolioName,
  SheetSummary,
  SheetInfo,
  AssetCount,
  CategoryCount,
  CurrencyRate,
  AggregatedSummary,
  CategoryTotal,
  PluginEvent,
  LogLevel,
  ChatMessage,
  InferenceOpts,
  PluginMetadata,
};

/**
 * Gate denial, validation failure, or downstream error thrown by an `hq.*`
 * call. Mirrors the WIT `api-error` record: `code` is a stable machine token
 * (e.g. `"permission-denied"`, `"rate-limited"`, `"not-found"`), `message` is
 * safe to show the user. Carries no secret, raw prompt/response, credential id,
 * or request id (the AI-harness boundary rules).
 *
 * jco throws the raw record (a plain object `{ code, message }`), not an
 * `Error` instance. {@link isApiError} narrows an unknown caught value.
 */
export type ApiError = _ApiError;

/** Narrow a caught value to an {@link ApiError}. */
export function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === "object" &&
    e !== null &&
    typeof (e as ApiError).code === "string" &&
    typeof (e as ApiError).message === "string"
  );
}

/// Run a gated raw host call, normalising jco's error wrapping. jco raises a
/// host import's `result` err as a `ComponentError`-like `Error` whose
/// `.payload` holds the `{ code, message }` api-error record — NOT the record
/// itself. Unwrap it so the `hq.*` helpers throw the documented {@link ApiError}
/// record directly (so guest `catch` blocks can use {@link isApiError}).
/// Anything that isn't a wrapped api-error is rethrown unchanged.
function gated<T>(call: () => T): T {
  try {
    return call();
  } catch (e) {
    const payload = (e as { payload?: unknown } | null | undefined)?.payload;
    if (isApiError(payload)) throw payload;
    throw e;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// `hq.*` — the ergonomic, stable author API over the raw host imports.
//
// Each function maps 1:1 onto a `hellohq:plugin/*` import. Gated calls return
// the success value or THROW an `ApiError` (see the module-level error model).
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Read-only, permission-gated workspace data. Each call requires the matching
 * manifest permission; `readAggregatedValues` additionally requires Verified
 * trust + per-portfolio scope. Throws {@link ApiError} on denial.
 */
export const workspace = {
  /** Every portfolio's id + display name. (`read:portfolio_names`) */
  readPortfolioNames(): PortfolioName[] {
    return gated(() => rawWorkspace.readPortfolioNames());
  },
  /** Sheet/section names for a portfolio (never values). (`read:sheet_structure`) */
  readSheetStructure(portfolioId: string): SheetSummary {
    return gated(() => rawWorkspace.readSheetStructure(portfolioId));
  },
  /** Item counts split by category for a portfolio. (`read:asset_count`) */
  readAssetCount(portfolioId: string): AssetCount {
    return gated(() => rawWorkspace.readAssetCount(portfolioId));
  },
  /** Workspace currencies and exchange rates. (`read:currency_rates`) */
  readCurrencyRates(): CurrencyRate[] {
    return gated(() => rawWorkspace.readCurrencyRates());
  },
  /** Per-category portfolio totals. (`read:aggregated_values`, Verified tier) */
  readAggregatedValues(portfolioId: string): AggregatedSummary {
    return gated(() => rawWorkspace.readAggregatedValues(portfolioId));
  },
} as const;

/**
 * Per-plugin key-value storage. Host-keyed by `(plugin_id, key)`; the host
 * enforces a per-plugin quota. Throws {@link ApiError} on quota/denial.
 */
export const storage = {
  /** Read a value. Returns `undefined` if the key is absent. */
  get(key: string): Uint8Array | undefined {
    return gated(() => rawStorage.get(key));
  },
  /** Write a value (overwrites). */
  set(key: string, value: Uint8Array): void {
    gated(() => rawStorage.set(key, value));
  },
  /** Delete a key (no-op if absent). */
  delete(key: string): void {
    gated(() => rawStorage.delete(key));
  },
  /** Delete all of this plugin's keys. */
  clear(): void {
    gated(() => rawStorage.clear());
  },
  /** List all keys this plugin has stored (keys only, no values). */
  listKeys(): string[] {
    return gated(() => rawStorage.listKeys());
  },
} as const;

/** Push events from the plugin to the host (size + rate capped host-side). */
export const events = {
  /** Emit an event. `kind` is a stable tag; `payload` is opaque bytes. */
  emit(kind: string, payload: Uint8Array): void {
    gated(() => rawEvents.emit({ kind, payload }));
  },
  /** Emit a pre-built {@link PluginEvent}. */
  emitEvent(event: PluginEvent): void {
    gated(() => rawEvents.emit(event));
  },
} as const;

/** Structured logging. Always available, never permission-gated. */
export const log = {
  /** Log at an explicit level. */
  write(level: LogLevel, message: string): void {
    rawLog.write(level, message);
  },
  trace(message: string): void {
    rawLog.write("trace", message);
  },
  debug(message: string): void {
    rawLog.write("debug", message);
  },
  info(message: string): void {
    rawLog.write("info", message);
  },
  warn(message: string): void {
    rawLog.write("warn", message);
  },
  error(message: string): void {
    rawLog.write("error", message);
  },
} as const;

/**
 * AI inference, routed through the host's gated HQAuthProxy. `complete`
 * **streams** token deltas as a `ReadableStream<string>` — one UTF-8 delta per
 * chunk.
 *
 * IMPORTANT: the pinned componentize-js (0.21.0) cannot componentize a world
 * that imports a WIT `stream` type, so the default build world omits
 * `inference`. Calling these helpers will fail at `jco componentize` until the
 * engine gains stream support (see `wit/README.md`). They are kept for
 * forward-compatibility and parity with the Rust SDK.
 */
export const inference = {
  /** Build a `user`-role message. */
  user(content: string): ChatMessage {
    return { role: "user", content };
  },
  /** Build a `system`-role message. */
  system(content: string): ChatMessage {
    return { role: "system", content };
  },
  /** Build an `assistant`-role message. */
  assistant(content: string): ChatMessage {
    return { role: "assistant", content };
  },
  /** Start a streaming completion. Resolves to a `ReadableStream<string>` of
   *  token deltas, or throws {@link ApiError} on gate denial / validation
   *  failure.
   *
   *  Async because the `inference` host module is loaded lazily (see the note
   *  on the raw imports above). On the pinned componentize-js this call cannot
   *  be built into a component at all — it is here for forward-compatibility. */
  async complete(
    messages: ChatMessage[],
    opts: InferenceOpts,
  ): Promise<ReadableStream<string>> {
    const raw = (await import("hellohq:plugin/inference@0.1.0")) as {
      complete(m: ChatMessage[], o: InferenceOpts): ReadableStream<string>;
    };
    return gated(() => raw.complete(messages, opts));
  },
  /** Drain a token-delta stream to a single string, concatenating each delta. */
  async collect(stream: ReadableStream<string>): Promise<string> {
    let out = "";
    const reader = stream.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value !== undefined) out += value;
    }
    return out;
  },
} as const;

/** The ergonomic capability namespace — `hq.workspace`, `hq.storage`, … */
export const hq = { workspace, storage, events, log, inference } as const;

// ─────────────────────────────────────────────────────────────────────────────
// Plugin entry point: the `guest` exports (`init` / `run` / `metadata`).
// ─────────────────────────────────────────────────────────────────────────────

/**
 * The three exports every HelloHQ Tier-2 plugin provides (the canonical
 * `hellohq:plugin/guest` interface). The JS analogue of the Rust `Plugin`
 * trait.
 *
 * - `init` runs once after instantiation, before any `run` (optional).
 * - `run(input)` is the main entry point. `input` is opaque bytes; the returned
 *   bytes are handed back to the host.
 *
 *   **You MUST catch errors inside `run`.** A gated `hq.*` call throws an
 *   {@link ApiError} on denial, and an *uncaught* throw does NOT map to the WIT
 *   `run`'s `Err(string)` — in the current jco/componentize-js it **traps the
 *   guest** (a hard wasm `unreachable`). So wrap gated calls in `try/catch` and
 *   **return** a degraded value (the JS analogue of Rust's
 *   `read().map_err(|e| e.message)?`). See `examples/component-quickstart-js`.
 * - `metadata` returns the plugin's static identity.
 */
export interface PluginDef {
  init?(): void;
  run(input: Uint8Array): Uint8Array;
  metadata(): PluginMetadata;
}

/**
 * Define a plugin's `guest` exports. The return value is the object jco expects
 * the componentized module to export under the name `guest`:
 *
 * ```ts
 * import { hq, definePlugin } from "@hellohq/plugin-sdk/component";
 *
 * export const guest = definePlugin({
 *   init() { hq.log.info("my-plugin starting"); },
 *   run(_input) {
 *     const names = hq.workspace.readPortfolioNames();
 *     hq.storage.set("count", new Uint8Array([names.length]));
 *     hq.events.emit("scanned", new TextEncoder().encode("ok"));
 *     return new TextEncoder().encode(`${names.length} portfolios`);
 *   },
 *   metadata() { return { id: "my-plugin", version: "0.1.0" }; },
 * });
 * ```
 *
 * The export name **must** be `guest` (it matches `export guest;` in the WIT
 * world). `init` defaults to a no-op if omitted.
 */
export function definePlugin(def: PluginDef): Required<PluginDef> {
  return {
    init: def.init ?? (() => {}),
    run: def.run,
    metadata: def.metadata,
  };
}
