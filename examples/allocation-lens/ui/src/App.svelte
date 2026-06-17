<!--
  Allocation Lens — WebView UI (Svelte 5).

  Two host interactions, mediated by the host-injected `window.HQBridge` that
  `@hellohq/plugin-sdk`'s `HQHost` wraps:

    1. Reads (no Wasm): host.readPortfolioNames() + host.readAssetCount(id) use
       the bridge `read` action. From these we build a COMPACT context string —
       portfolio names and per-category item counts only, NEVER raw values.
    2. Compute (Wasm): host.compute("narrate", { context }) runs the plugin's
       component run({function,args}), which feeds the context to the host AI
       and returns { narrative }. This is the example's primary path.
-->
<script lang="ts">
  import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

  /** Shape the Wasm component returns from run() (see ../../src/lib.rs). */
  interface Narrative {
    narrative: string;
  }

  type State =
    | { kind: "loading" }
    | { kind: "empty"; title: string; description: string }
    | { kind: "error"; message: string }
    | { kind: "ready"; narrative: string };

  const host = new HQHost();

  let state = $state<State>({ kind: "loading" });

  /**
   * Build the model context from portfolio names + per-category asset counts.
   * Only names and counts cross into the prompt — no monetary values.
   */
  function buildContext(
    names: { id: string; name: string }[],
    counts: { portfolioId: string; byCategory: { category: string; count: number }[] }[],
  ): string {
    const lines = counts.map((c) => {
      const label = names.find((n) => n.id === c.portfolioId)?.name ?? c.portfolioId;
      const total = c.byCategory.reduce((sum, b) => sum + b.count, 0);
      const breakdown = c.byCategory
        .map((b) => `${b.category}: ${b.count}`)
        .join(", ");
      return `- ${label}: ${total} item(s) [${breakdown || "no categories"}]`;
    });
    return `Portfolio structure (item counts only, no values):\n${lines.join("\n")}`;
  }

  async function generate(): Promise<void> {
    state = { kind: "loading" };
    try {
      const names = await host.readPortfolioNames();
      if (names.length === 0) {
        state = {
          kind: "empty",
          title: "No portfolios",
          description: "Create a portfolio in HelloHQ to generate an allocation narrative.",
        };
        return;
      }

      const counts = await Promise.all(names.map((n) => host.readAssetCount(n.id)));
      const context = buildContext(names, counts);

      const data = await host.compute<Narrative>("narrate", { context });
      const narrative = data.narrative.trim();
      if (!narrative) {
        state = {
          kind: "empty",
          title: "No narrative",
          description: "The model returned an empty response. Try regenerating.",
        };
        return;
      }
      state = { kind: "ready", narrative };
    } catch (e) {
      state = { kind: "error", message: describeError(e) };
    }
  }

  function describeError(e: unknown): string {
    if (e instanceof HQPermissionError) {
      return `Permission needed: grant ${e.permissionId} in the plugin manifest.`;
    }
    return e instanceof Error ? e.message : String(e);
  }

  // Initial render: exercise the read -> compute pipeline.
  void generate();
</script>

<main class="app" aria-busy={state.kind === "loading"}>
  <div class="header">
    <h2>Allocation Lens</h2>
    <button onclick={() => void generate()} disabled={state.kind === "loading"}>
      {state.kind === "loading" ? "Working…" : "Regenerate"}
    </button>
  </div>

  {#if state.kind === "loading"}
    <div class="loading">Analyzing your allocation…</div>
  {:else if state.kind === "empty"}
    <div class="empty">
      <div class="title">{state.title}</div>
      <div class="desc">{state.description}</div>
    </div>
  {:else if state.kind === "error"}
    <div class="error">{state.message}</div>
  {:else}
    <p class="narrative">{state.narrative}</p>
    <p class="footnote">
      Generated from portfolio names and item counts only — no account values are
      sent to the model.
    </p>
  {/if}
</main>
