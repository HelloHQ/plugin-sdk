// SPDX-License-Identifier: Apache-2.0
//
// Portfolio Summary — WebView UI logic (framework-agnostic, vanilla TS).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py). This file uses
// only the DOM + the typed HQHost wrapper; the same flow works from any
// framework. It demonstrates the bridge against a *sidecar*:
//   1. host.compute("summary", {})  -> {portfolios, count}
//   2. a "Refresh" button re-runs the summary.

import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

interface Portfolio {
  name: string;
  total?: unknown;
}

interface Summary {
  portfolios: Portfolio[];
  count: number;
}

const host = new HQHost();
const app = document.getElementById("app")!;

async function load(): Promise<void> {
  setBusy(true);
  try {
    const summary = await host.compute<Summary>("summary", {});
    renderSummary(summary);
  } catch (e) {
    renderError(e);
  } finally {
    setBusy(false);
  }
}

function renderSummary(summary: Summary): void {
  clear();

  const header = el("div", "header");
  const title = el("h2");
  title.textContent = `${summary.count} Portfolio${summary.count === 1 ? "" : "s"}`;

  const refresh = document.createElement("button");
  refresh.textContent = "Refresh";
  refresh.addEventListener("click", () => void load());

  header.append(title, refresh);
  app.append(header);

  if (!summary.portfolios.length) {
    const empty = el("div", "empty");
    empty.textContent = "No portfolios to summarise.";
    app.append(empty);
    return;
  }

  const list = el("ul", "list");
  for (const p of summary.portfolios) {
    const row = el("li", "row");

    const name = el("span", "name");
    name.textContent = p.name || "(unnamed)";

    const value = el("span", "value");
    if (p.total !== undefined && p.total !== null) {
      value.textContent = String(p.total);
    }

    row.append(name, value);
    list.append(row);
  }
  app.append(list);
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
  app.append(box);
}

function el(tag: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function clear(): void {
  app.replaceChildren();
}

function setBusy(busy: boolean): void {
  app.setAttribute("aria-busy", busy ? "true" : "false");
}

void load();
