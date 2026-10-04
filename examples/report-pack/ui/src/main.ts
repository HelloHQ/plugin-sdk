// SPDX-License-Identifier: Apache-2.0
//
// Report Pack — WebView UI logic (framework-agnostic, vanilla TS).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py): it builds the
// report and renders it as Markdown, plain text and HTML in the chosen
// language. This file only (1) asks for the report, (2) previews it as plain
// text (never as HTML — the preview uses textContent), and (3) saves the chosen
// format through the OS save dialog with HQHost.writeExternal
// (write:external_output). The plugin never sees the path the person picks.

import { HQHost, HQHostError, HQPermissionError } from "@hellohq/plugin-sdk";

type Lang = "en" | "zh-Hans";
type Format = "markdown" | "html" | "text";

interface ReportDocument {
  filename: string;
  mime: string;
  content: string;
}

interface Report {
  lang: Lang;
  generated_at: string;
  data_status: "complete" | "partial" | "withheld" | "no_totals" | "no_portfolios";
  documents: Record<Format, ReportDocument>;
}

// Chrome strings only: the report text itself is produced (and translated) by
// the sidecar.
const UI: Record<Lang, Record<string, string>> = {
  en: {
    title: "Family meeting report",
    language: "Language",
    loading: "Preparing report…",
    ready: "Report prepared {time} UTC. Information only, not advice.",
    saveMarkdown: "Save as Markdown",
    saveHtml: "Save as HTML",
    saveText: "Save as text",
    saved: "Saved {name}.",
    cancelled: "Save cancelled. Nothing was written.",
    saveDenied: "Saving files is not permitted for this plugin.",
    error: "The report could not be prepared: {message}",
    denied: "Permission denied: {permission}",
    saveError: "The file could not be saved: {message}",
  },
  "zh-Hans": {
    title: "家庭会议报告",
    language: "语言",
    loading: "正在生成报告…",
    ready: "报告编制时间：{time} UTC。仅供信息参考，不构成建议。",
    saveMarkdown: "保存为 Markdown",
    saveHtml: "保存为 HTML",
    saveText: "保存为文本",
    saved: "已保存 {name}。",
    cancelled: "已取消保存，未写入任何内容。",
    saveDenied: "此插件无权保存文件。",
    error: "无法生成报告：{message}",
    denied: "权限被拒绝：{permission}",
    saveError: "无法保存文件：{message}",
  },
};

const host = new HQHost();
const app = document.getElementById("app")!;

let lang: Lang = initialLang();
let current: Report | null = null;
let statusEl: HTMLElement;
let previewEl: HTMLElement;
let buttons: HTMLButtonElement[] = [];
// Each load gets a number; a reply for an older load (e.g. after a quick
// language switch) is dropped so the preview never shows the wrong language.
let loadSeq = 0;

/** zh-CN / zh-SG / zh-Hans -> Simplified Chinese; anything else -> English. */
function initialLang(): Lang {
  const tag = (navigator.language || "en").toLowerCase();
  return tag === "zh" || tag === "zh-cn" || tag === "zh-sg" || tag.startsWith("zh-hans")
    ? "zh-Hans"
    : "en";
}

function t(key: string, vars: Record<string, string> = {}): string {
  let text = UI[lang][key] ?? key;
  for (const [name, value] of Object.entries(vars)) {
    text = text.replace(`{${name}}`, value);
  }
  return text;
}

async function load(): Promise<void> {
  const seq = ++loadSeq;
  current = null;
  render();
  setStatus(t("loading"));
  setBusy(true);
  try {
    // Only a primitive argument: the host bridge rejects nested compute args.
    const report = await host.compute<Report>("report", { lang });
    if (seq !== loadSeq) return;
    current = report;
    previewEl.textContent = report.documents.text.content;
    setStatus(t("ready", { time: report.generated_at.slice(0, 16).replace("T", " ") }));
    buttons.forEach((b) => (b.disabled = false));
  } catch (e) {
    if (seq !== loadSeq) return;
    previewEl.textContent = "";
    renderError(e);
  } finally {
    if (seq === loadSeq) setBusy(false);
  }
}

async function save(format: Format): Promise<void> {
  if (!current) return;
  const doc = current.documents[format];
  buttons.forEach((b) => (b.disabled = true));
  try {
    const { saved } = await host.writeExternal(doc.filename, doc.content);
    setStatus(saved ? t("saved", { name: doc.filename }) : t("cancelled"));
  } catch (e) {
    setStatus(e instanceof HQPermissionError ? t("saveDenied") : t("saveError", { message: message(e) }));
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function render(): void {
  document.documentElement.lang = lang;
  document.title = t("title");
  app.replaceChildren();

  const toolbar = el("div", "toolbar");

  const label = document.createElement("label");
  label.htmlFor = "lang";
  label.textContent = t("language");

  const select = document.createElement("select");
  select.id = "lang";
  for (const [value, text] of [
    ["en", "English"],
    ["zh-Hans", "简体中文"],
  ] as const) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    option.selected = value === lang;
    select.append(option);
  }
  select.addEventListener("change", () => {
    lang = select.value as Lang;
    void load();
  });

  const spacer = el("span", "spacer");
  buttons = [
    saveButton("saveMarkdown", "markdown"),
    saveButton("saveHtml", "html"),
    saveButton("saveText", "text"),
  ];
  toolbar.append(label, select, spacer, ...buttons);

  statusEl = el("p", "status");
  statusEl.setAttribute("role", "status");
  previewEl = el("pre", "preview");
  previewEl.setAttribute("lang", lang);

  app.append(toolbar, statusEl, previewEl);
}

function saveButton(labelKey: string, format: Format): HTMLButtonElement {
  const button = document.createElement("button");
  button.textContent = t(labelKey);
  button.disabled = true;
  button.addEventListener("click", () => void save(format));
  return button;
}

function renderError(e: unknown): void {
  setStatus(
    e instanceof HQPermissionError
      ? t("denied", { permission: e.permissionId })
      : t("error", { message: message(e) }),
  );
}

function message(e: unknown): string {
  return e instanceof HQHostError || e instanceof Error ? e.message : String(e);
}

function el(tag: string, className?: string): HTMLElement {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function setStatus(text: string): void {
  statusEl.textContent = text;
}

function setBusy(busy: boolean): void {
  app.setAttribute("aria-busy", busy ? "true" : "false");
}

void load();
