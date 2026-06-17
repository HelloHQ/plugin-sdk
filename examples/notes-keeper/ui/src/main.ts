// SPDX-License-Identifier: Apache-2.0
//
// Notes Keeper — WebView UI logic (framework-agnostic, vanilla TS).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py). This file uses
// only the DOM + the typed HQHost wrapper; the same flow works from any
// framework. It demonstrates three bridge interactions against a *sidecar*:
//   1. host.compute("run", {})            -> load {count, note}
//   2. host.compute("save_note", {note})  -> persist, returns {saved, note}
//   3. host.on("note-saved", cb)          -> a push event the sidecar emits via
//      emit_event (Tier-1 sidecars can drive WebView push events too).

import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

interface State {
  count: number;
  note: string;
}

const host = new HQHost();
const app = document.getElementById("app")!;

let textarea: HTMLTextAreaElement;
let saveBtn: HTMLButtonElement;
let status: HTMLElement;

// Confirm saves via the sidecar's push event (in addition to the compute
// result) — proves the Tier-1 emit_event -> HQBridge.on path end to end.
host.on("note-saved", () => {
  setStatus("Saved.", true);
});

async function load(): Promise<void> {
  setBusy(true);
  try {
    const state = await host.compute<State>("run", {});
    renderEditor(state);
  } catch (e) {
    renderError(e);
  } finally {
    setBusy(false);
  }
}

async function save(): Promise<void> {
  saveBtn.disabled = true;
  setStatus("Saving…", false);
  try {
    await host.compute<{ saved: boolean }>("save_note", { note: textarea.value });
    // The "note-saved" push event sets the final status; nothing else to do.
  } catch (e) {
    setStatus(e instanceof Error ? e.message : String(e), false);
  } finally {
    saveBtn.disabled = false;
  }
}

function renderEditor(state: State): void {
  clear();

  const title = el("h2");
  title.textContent = "Notes Keeper";

  const count = el("div", "count");
  count.textContent = `Run ${state.count} time${state.count === 1 ? "" : "s"}`;

  const label = el("label");
  label.textContent = "Note (persisted via plugin:storage)";

  textarea = document.createElement("textarea");
  textarea.value = state.note;
  textarea.addEventListener("input", () => setStatus("", false));

  saveBtn = document.createElement("button");
  saveBtn.textContent = "Save";
  saveBtn.addEventListener("click", () => void save());

  status = el("span", "status");

  const actions = el("div", "actions");
  actions.append(saveBtn, status);

  app.append(title, count, label, textarea, actions);
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

function setStatus(text: string, ok: boolean): void {
  if (!status) return;
  status.textContent = text;
  status.className = ok ? "status ok" : "status";
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
