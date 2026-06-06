/**
 * @hellohq/plugin-sdk — Tier 2 (WebView) helper surface.
 *
 * For WebView plugins, the host injects a validated `HQBridge` channel. This
 * SDK wraps it in a typed `HQHost` so plugin UIs never hand-roll postMessage.
 * Data and compute requests are mediated by the host — the WebView never calls
 * the Wasm binary or the network directly.
 *
 * Status: skeleton (Phase 4 — begin). Surface is stable; transport wiring lands
 * with the WebView host (Phase 6).
 */

export const PROTOCOL_VERSION = "1.0.0";

export interface PortfolioName {
  id: string;
  name: string;
}

export interface AggregatedSummary {
  portfolioId: string;
  currency: string;
  totalValue: number;
  asOfTimestamp: number;
}

export class HQPermissionError extends Error {
  constructor(public readonly permissionId: string) {
    super(`permission denied: ${permissionId}`);
    this.name = "HQPermissionError";
  }
}

type Handler = (payload: unknown) => void;

interface BridgeMessage {
  action: string;
  payload?: unknown;
}

declare global {
  interface Window {
    HQBridge?: { postMessage(raw: string): void };
  }
}

/**
 * Typed wrapper over the host-injected `HQBridge`.
 */
export class HQHost {
  private readonly handlers = new Map<string, Set<Handler>>();

  /** Read permission-gated data from the host. */
  async readPortfolioNames(): Promise<PortfolioName[]> {
    return this.request<PortfolioName[]>("read", { resource: "portfolio_names" });
  }

  async readAggregatedValues(portfolioId: string): Promise<AggregatedSummary> {
    return this.request<AggregatedSummary>("read", {
      resource: "aggregated_values",
      portfolioId,
    });
  }

  /** Invoke the plugin's Wasm binary through the host. */
  async compute<T>(fn: string, args: unknown): Promise<T> {
    return this.request<T>("compute", { function: fn, args });
  }

  /** Subscribe to a push event emitted by the Wasm binary / sidecar. */
  on(name: string, handler: Handler): () => void {
    const set = this.handlers.get(name) ?? new Set();
    set.add(handler);
    this.handlers.set(name, set);
    return () => set.delete(handler);
  }

  // --- transport (skeleton) -------------------------------------------------

  private request<T>(action: string, payload: unknown): Promise<T> {
    // TODO(phase6): correlate responses via the host's reply channel.
    this.post({ action, payload });
    return Promise.reject(
      new Error("HQHost transport not yet wired (Phase 6 — WebView host)"),
    );
  }

  private post(msg: BridgeMessage): void {
    const bridge = window.HQBridge;
    if (!bridge) throw new Error("HQBridge unavailable — not running in a HelloHQ WebView");
    bridge.postMessage(JSON.stringify(msg));
  }
}
