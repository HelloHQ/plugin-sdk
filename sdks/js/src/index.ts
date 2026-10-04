/**
 * @hellohq/plugin-sdk — Tier 2 (WebView) helper surface.
 *
 * For WebView plugins, the host injects a validated `HQBridge` channel. This
 * SDK wraps it in a typed `HQHost` so plugin UIs never hand-roll messages.
 * Data and compute requests are mediated by the host — the WebView never calls
 * the Wasm binary or the network directly.
 *
 * Transport
 * ─────────
 * The HelloHQ app injects `window.HQBridge` with typed, Promise-returning
 * methods before any page script runs (hellohq
 * `lib/app/utils/service/plugin_webview_init_script.dart`):
 *
 *   HQBridge.read(resource, args)              -> { action: "read", payload: { resource, args } }
 *   HQBridge.compute(fn, args)                 -> { action: "compute", payload: { function, args } }
 *   HQBridge.writeExternal(name, base64)       -> { action: "write_external",
 *                                                   payload: { suggested_filename, content_base64 } }
 *
 * Each resolves with the host's `data` or rejects with an `Error` carrying
 * `code` (`permission_denied` | `compute_error` | `internal` | `bad_request`).
 * `HQHost` uses these methods whenever they are present.
 *
 * Legacy transport (dev harnesses and older shims that expose only
 * `postMessage`):
 *   Outbound: window.HQBridge.postMessage(JSON.stringify({id, action, payload}))
 *   Inbound:  window "message" events carrying
 *     • RPC response:  { id: number, data: T }
 *     • RPC error:     { id: number, error: { code: string, message: string } }
 *     • Push event:    { event: string, payload: unknown }
 */

export const PROTOCOL_VERSION = "0.1.0";

// ─────────────────────────────────────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────────────────────────────────────

export interface PortfolioName {
  id: string;
  name: string;
}

export interface SheetSummary {
  portfolioId: string;
  sheets: Sheet[];
}

export interface Sheet {
  id: string;
  name: string;
  category: string;
  itemCount: number;
}

export interface AssetCount {
  portfolioId: string;
  byCategory: CategoryCount[];
  total: number;
}

export interface CategoryCount {
  category: string;
  count: number;
}

export interface AggregatedSummary {
  portfolioId: string;
  currency: string;
  totalValue: number;
  asOfTimestamp: number;
}

export interface CurrencyRate {
  id: string;
  name: string;
  symbol: string;
  rate: number;
}

export class HQPermissionError extends Error {
  constructor(public readonly permissionId: string) {
    super(`permission denied: ${permissionId}`);
    this.name = "HQPermissionError";
  }
}

export class HQHostError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "HQHostError";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal types
// ─────────────────────────────────────────────────────────────────────────────

type Handler = (payload: unknown) => void;
type PendingEntry = { resolve: (v: unknown) => void; reject: (e: unknown) => void };

interface OutboundMessage {
  id: number;
  action: string;
  payload?: unknown;
}

/** The host-injected bridge: the app's typed shim and/or a legacy channel. */
export interface HQBridgeShim {
  read?(resource: string, args?: Record<string, unknown>): Promise<unknown>;
  compute?(fn: string, args?: unknown): Promise<unknown>;
  writeExternal?(suggestedFilename: string, contentBase64: string): Promise<unknown>;
  log?(level: string, message: string): void;
  postMessage?(raw: string): void;
}

declare global {
  interface Window {
    HQBridge?: HQBridgeShim;
  }
}

/** Largest file the host's `write_external` accepts (bytes, before base64). */
export const MAX_WRITE_EXTERNAL_BYTES = 50 * 1024 * 1024;

/**
 * Why the host would refuse `suggestedFilename`, or null if it is acceptable.
 * Mirrors `PluginWebViewBridge._unsafeFilename` in the app exactly: non-empty,
 * at most 255 UTF-16 code units, and no `/`, `\`, `..` or NUL anywhere.
 */
export function writeExternalFilenameProblem(suggestedFilename: string): string | null {
  if (typeof suggestedFilename !== "string" || suggestedFilename.length === 0) {
    return "suggested_filename must be a non-empty string";
  }
  if (suggestedFilename.length > 255) return "suggested_filename is longer than 255 characters";
  if (["/", "\\", "..", "\u0000"].some((bad) => suggestedFilename.includes(bad))) {
    return "suggested_filename must not contain a path separator, \"..\" or NUL";
  }
  return null;
}

function toHostError(e: unknown): Error {
  if (e instanceof HQHostError || e instanceof HQPermissionError) return e;
  const obj = (e ?? {}) as { code?: unknown; message?: unknown };
  const code = typeof obj.code === "string" ? obj.code : "internal";
  const message = typeof obj.message === "string" ? obj.message : String(e);
  // The host puts the permission id in the message of a permission_denied.
  return code === "permission_denied" ? new HQPermissionError(message) : new HQHostError(code, message);
}

/** Base64 of raw bytes (chunked so large files do not overflow the call stack). */
function toBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

// ─────────────────────────────────────────────────────────────────────────────
// HQHost
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Typed wrapper over the host-injected `HQBridge`.
 *
 * Create one instance per plugin page. Call `dispose()` when the page unmounts
 * to remove the message listener and reject in-flight requests.
 */
export class HQHost {
  private readonly handlers = new Map<string, Set<Handler>>();
  private readonly pending = new Map<number, PendingEntry>();
  private nextId = 0;
  private readonly _listener: (e: MessageEvent) => void;

  constructor() {
    this._listener = (e: MessageEvent) => this._dispatch(e.data);
    window.addEventListener("message", this._listener);
  }

  /** Remove the message listener and reject any in-flight requests. */
  dispose(): void {
    window.removeEventListener("message", this._listener);
    for (const { reject } of this.pending.values()) {
      reject(new HQHostError("disposed", "HQHost was disposed"));
    }
    this.pending.clear();
  }

  // ── Permission-gated data reads ────────────────────────────────────────────

  /** Requires read:portfolio_names. */
  readPortfolioNames(): Promise<PortfolioName[]> {
    return this.request<PortfolioName[]>("read", { resource: "portfolio_names" });
  }

  /** Requires read:sheet_structure. */
  readSheetStructure(portfolioId: string): Promise<SheetSummary> {
    return this.request<SheetSummary>("read", { resource: "sheet_structure", portfolioId });
  }

  /** Requires read:asset_count. */
  readAssetCount(portfolioId: string): Promise<AssetCount> {
    return this.request<AssetCount>("read", { resource: "asset_count", portfolioId });
  }

  /** Requires read:currency_rates. */
  readCurrencyRates(): Promise<CurrencyRate[]> {
    return this.request<CurrencyRate[]>("read", { resource: "currency_rates" });
  }

  /** Requires read:aggregated_values (Verified tier). */
  readAggregatedValues(portfolioId: string): Promise<AggregatedSummary> {
    return this.request<AggregatedSummary>("read", {
      resource: "aggregated_values",
      portfolioId,
    });
  }

  /**
   * Invoke the plugin's compute binary through the host. The host bridge
   * accepts only JSON primitives or flat arrays of primitives as `args` values
   * (no nested objects, and no `null` on the current app host).
   */
  compute<T>(fn: string, args: Record<string, unknown>): Promise<T> {
    return this.request<T>("compute", { function: fn, args });
  }

  /**
   * Save a file through the OS save dialog. Requires write:external_output
   * (Verified tier; wired only for WebView UIs).
   *
   * The person picks the destination; the plugin never learns the path.
   * Resolves `{ saved: true }` once written, or `{ saved: false }` if the person
   * cancelled the dialog. A string is encoded as UTF-8 (no BOM); bytes are sent
   * unchanged. The filename and size rules the host enforces are checked here
   * first (see {@link writeExternalFilenameProblem} and
   * {@link MAX_WRITE_EXTERNAL_BYTES}) and reject with `bad_request`.
   */
  writeExternal(
    suggestedFilename: string,
    content: string | Uint8Array,
  ): Promise<{ saved: boolean }> {
    const problem = writeExternalFilenameProblem(suggestedFilename);
    if (problem) return Promise.reject(new HQHostError("bad_request", problem));
    const bytes = typeof content === "string" ? new TextEncoder().encode(content) : content;
    if (!(bytes instanceof Uint8Array)) {
      return Promise.reject(new HQHostError("bad_request", "content must be a string or Uint8Array"));
    }
    if (bytes.length > MAX_WRITE_EXTERNAL_BYTES) {
      return Promise.reject(new HQHostError("bad_request", "File exceeds the 50 MB limit"));
    }
    return this.request<{ saved: boolean }>("write_external", {
      suggested_filename: suggestedFilename,
      content_base64: toBase64(bytes),
    });
  }

  /** Subscribe to a push event emitted by the Wasm binary / sidecar. */
  on(name: string, handler: Handler): () => void {
    const set = this.handlers.get(name) ?? new Set<Handler>();
    set.add(handler);
    this.handlers.set(name, set);
    return () => set.delete(handler);
  }

  // ── Transport ─────────────────────────────────────────────────────────────

  private request<T>(action: string, payload: Record<string, unknown>): Promise<T> {
    const bridge = window.HQBridge;
    if (!bridge) {
      return Promise.reject(
        new HQHostError(
          "bridge_unavailable",
          "HQBridge unavailable — not running in a HelloHQ WebView",
        ),
      );
    }
    const viaShim = HQHost._shimCall(bridge, action, payload);
    if (viaShim) {
      return viaShim.then(
        (data) => data as T,
        (e) => {
          throw toHostError(e);
        },
      );
    }
    if (typeof bridge.postMessage !== "function") {
      return Promise.reject(
        new HQHostError("bridge_unavailable", `HQBridge has no transport for "${action}"`),
      );
    }
    const id = this.nextId++;
    const promise = new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: resolve as (v: unknown) => void,
        reject,
      });
    });
    try {
      bridge.postMessage(JSON.stringify({ id, action, payload } satisfies OutboundMessage));
    } catch (e) {
      this.pending.delete(id);
      return Promise.reject(toHostError(e));
    }
    return promise;
  }

  /**
   * Route a call through the app's typed shim when it has the method, mapping
   * this SDK's payload onto the shim's positional arguments. Returns null when
   * the shim lacks the method (legacy transport).
   */
  private static _shimCall(
    bridge: HQBridgeShim,
    action: string,
    payload: Record<string, unknown>,
  ): Promise<unknown> | null {
    const call = (fn: () => Promise<unknown>) =>
      new Promise<unknown>((resolve, reject) => {
        try {
          resolve(fn());
        } catch (e) {
          reject(e);
        }
      });
    switch (action) {
      case "read": {
        if (typeof bridge.read !== "function") return null;
        const { resource, portfolioId } = payload;
        const args = typeof portfolioId === "string" ? { portfolio_id: portfolioId } : {};
        return call(() => bridge.read!(resource as string, args));
      }
      case "compute":
        if (typeof bridge.compute !== "function") return null;
        return call(() => bridge.compute!(payload["function"] as string, payload["args"]));
      case "write_external":
        if (typeof bridge.writeExternal !== "function") return null;
        return call(() =>
          bridge.writeExternal!(
            payload["suggested_filename"] as string,
            payload["content_base64"] as string,
          ),
        );
      default:
        return null;
    }
  }

  private _dispatch(raw: unknown): void {
    // Normalise: host may send a JSON string or a parsed object.
    let msg: unknown = raw;
    if (typeof msg === "string") {
      try {
        msg = JSON.parse(msg);
      } catch {
        return;
      }
    }
    if (!msg || typeof msg !== "object") return;
    const obj = msg as Record<string, unknown>;

    // Push event (no id; carries an "event" key).
    if (typeof obj["event"] === "string") {
      const handlers = this.handlers.get(obj["event"] as string);
      if (handlers) {
        for (const h of handlers) h(obj["payload"]);
      }
      return;
    }

    // RPC response (carries an integer "id").
    const id = obj["id"];
    if (typeof id !== "number") return;
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);

    if ("error" in obj) {
      const err = obj["error"] as Record<string, unknown> | null | undefined;
      const code = (err?.["code"] as string | undefined) ?? "error";
      const message = (err?.["message"] as string | undefined) ?? "unknown error";
      if (code === "permission_denied") {
        const perm = (err?.["permission"] as string | undefined) ?? message;
        entry.reject(new HQPermissionError(perm));
      } else {
        entry.reject(new HQHostError(code, message));
      }
    } else {
      entry.resolve(obj["data"]);
    }
  }
}
