// SPDX-License-Identifier: Apache-2.0
//
// Portfolio Overview — WebView UI logic (framework-agnostic, vanilla TS).
//
// This file is intentionally framework-free: it uses only the DOM and the
// typed `HQHost` wrapper from `@hellohq/plugin-sdk`. The exact same pattern
// works from Svelte/Vue/React — the only contract with HelloHQ is the
// host-injected `window.HQBridge`, which `HQHost` wraps. See the other examples
// for the same flow expressed in those frameworks.
//
// Two host interactions are demonstrated:
//   1. host.compute("overview", {})  -> runs the plugin's Wasm `run()`, which
//      reads portfolio names (permission-gated) and returns a JSON document.
//      This is the example's primary path: UI <-> Wasm via the bridge.
//   2. host.readPortfolioNames()     -> a host-mediated permission-gated read,
//      shown as an alternative that never touches the Wasm binary.

import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

/** Shape the Wasm component returns from `run()` (see ../src/lib.rs). */
interface Overview {
  count: number;
  denied: boolean;
  portfolios: { id: string; name: string }[];
}

const host = new HQHost();
const app = document.getElementById("app")!;

/** Run the Wasm component and render its result. */
async function loadViaCompute(): Promise<void> {
  setBusy(true);
  try {
    const data = await host.compute<Overview>("overview", {});
    if (data.denied) {
      renderEmpty(
        "Permission needed",
        "Grant read:portfolio_names to list your portfolios.",
      );
    } else if (data.count === 0) {
      renderEmpty("No portfolios", "Create a portfolio in HelloHQ to see it here.");
    } else {
      renderList(data.portfolios);
    }
  } catch (e) {
    renderError(e);
  } finally {
    setBusy(false);
  }
}

/** Alternative path: read the names directly through the host (no Wasm). */
async function loadViaRead(): Promise<void> {
  setBusy(true);
  try {
    const names = await host.readPortfolioNames();
    if (names.length === 0) {
      renderEmpty("No portfolios", "Create a portfolio in HelloHQ to see it here.");
    } else {
      renderList(names);
    }
  } catch (e) {
    renderError(e);
  } finally {
    setBusy(false);
  }
}

// ── Rendering (plain DOM; no innerHTML with host data, to avoid injection) ────

function renderList(portfolios: { id: string; name: string }[]): void {
  clear();
  app.append(
    header(`${portfolios.length} portfolio${portfolios.length === 1 ? "" : "s"}`),
  );

  const list = el("ul", "list");
  portfolios.forEach((p, i) => {
    const row = el("li", "row");
    row.append(textSpan("index", `${i + 1}`), textSpan("name", p.name));
    list.append(row);
  });
  app.append(list);
}

function renderEmpty(title: string, description: string): void {
  clear();
  app.append(header(""));
  const box = el("div", "empty");
  box.append(textDiv("title", title), textDiv("desc", description));
  app.append(box);
}

function renderError(e: unknown): void {
  clear();
  const msg =
    e instanceof HQPermissionError
      ? `Permission denied: ${e.permissionId}`
      : e instanceof Error
        ? e.message
        : String(e);
  const box = el("div", "error");
  box.textContent = msg;
  app.append(header(""), box);
}

/** Header with the title + a "Recompute" button that re-runs the Wasm path. */
function header(countLabel: string): HTMLElement {
  const bar = el("div", "header");
  const left = el("div");
  const title = el("h2");
  title.textContent = "Portfolios";
  left.append(title);
  if (countLabel) {
    const c = el("span", "count");
    c.textContent = countLabel;
    left.append(document.createTextNode(" "), c);
  }

  const btn = document.createElement("button");
  btn.textContent = "Recompute";
  btn.addEventListener("click", () => void loadViaCompute());

  bar.append(left, btn);
  return bar;
}

// ── tiny DOM helpers ──────────────────────────────────────────────────────────

function el(tag: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function textSpan(className: string, text: string): HTMLElement {
  const s = el("span", className);
  s.textContent = text;
  return s;
}

function textDiv(className: string, text: string): HTMLElement {
  const d = el("div", className);
  d.textContent = text;
  return d;
}

function clear(): void {
  app.replaceChildren();
}

function setBusy(busy: boolean): void {
  app.setAttribute("aria-busy", busy ? "true" : "false");
}

// Mark loadViaRead as used (alternative path documented above; wired here so
// the bundle keeps it and authors can swap it in).
void loadViaRead;

// Initial render: exercise the UI <-> Wasm compute path.
void loadViaCompute();
