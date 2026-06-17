/**
 * @hellohq/plugin-sdk — Tier 2 (WebView) helper surface.
 *
 * For WebView plugins, the host injects a validated `HQBridge` channel. This
 * SDK wraps it in a typed `HQHost` so plugin UIs never hand-roll postMessage.
 * Data and compute requests are mediated by the host — the WebView never calls
 * the Wasm binary or the network directly.
 *
 * Transport protocol
 * ──────────────────
 * Outbound (plugin → host):  window.HQBridge.postMessage(JSON.stringify({id, action, payload}))
 * Inbound  (host → plugin):  window.dispatchEvent(new MessageEvent("message", { data: json }))
 *   • RPC response:  { id: number, data: T }
 *   • RPC error:     { id: number, error: { code: string, message: string } }
 *   • Push event:    { event: string, payload: unknown }
 *
 * The host dispatches responses as window MessageEvents so no separate channel
 * registration is required; the SDK installs one listener per HQHost instance.
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

declare global {
  interface Window {
    HQBridge?: { postMessage(raw: string): void };
  }
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

  /** Invoke the plugin's Wasm binary through the host. */
  compute<T>(fn: string, args: unknown): Promise<T> {
    return this.request<T>("compute", { function: fn, args });
  }

  /** Subscribe to a push event emitted by the Wasm binary / sidecar. */
  on(name: string, handler: Handler): () => void {
    const set = this.handlers.get(name) ?? new Set<Handler>();
    set.add(handler);
    this.handlers.set(name, set);
    return () => set.delete(handler);
  }

  // ── Transport ─────────────────────────────────────────────────────────────

  private request<T>(action: string, payload: unknown): Promise<T> {
    const id = this.nextId++;
    const promise = new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: resolve as (v: unknown) => void,
        reject,
      });
    });
    this._post({ id, action, payload });
    return promise;
  }

  private _post(msg: OutboundMessage): void {
    const bridge = window.HQBridge;
    if (!bridge) {
      throw new HQHostError(
        "bridge_unavailable",
        "HQBridge unavailable — not running in a HelloHQ WebView",
      );
    }
    bridge.postMessage(JSON.stringify(msg));
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
