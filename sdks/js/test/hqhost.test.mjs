// HQHost against the HelloHQ app's real HQBridge shim (test/fixtures/host-shim.js).
//
// The shim posts `{ id, action, payload }` over `window.ipc.postMessage`; the
// fake host below plays `PluginWebViewBridge.handle` in the app
// (lib/app/utils/service/plugin_webview_bridge.dart): it validates the message
// the same way and settles the call through `window.__hqResolve(response)`.
//
// Run: npm test  (builds dist/ first; Node 20+, no dependencies)

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, test } from "node:test";

globalThis.window = new EventTarget();
const { HQHost, HQHostError, HQPermissionError, MAX_WRITE_EXTERNAL_BYTES, writeExternalFilenameProblem } =
  await import("../dist/index.js");

const SHIM = readFileSync(new URL("./fixtures/host-shim.js", import.meta.url), "utf8");

// ── A fake host that mirrors PluginWebViewBridge ────────────────────────────

/** Port of PluginWebViewBridge._unsafeFilename. */
function unsafeFilename(name) {
  return name.includes("/") || name.includes("\\") || name.includes("..") ||
    name.includes("\x00") || name.length > 255;
}

let posted; // every raw message the page sent
let grants; // permission ids the fake gate allows
let saveOutcome; // "saved" | "cancelled" | "failed"
let savedFiles; // [{ name, bytes }]

function respond(response) {
  // The real controller evals `window.__hqResolve(<raw json>)` asynchronously.
  setTimeout(() => window.__hqResolve(JSON.parse(JSON.stringify(response))), 0);
}

function fakeHost(raw) {
  posted.push(raw);
  const msg = JSON.parse(raw);
  const { id, action } = msg;
  const payload = msg.payload ?? {};
  const err = (code, message) => respond({ id, ok: false, error: { code, message } });
  const ok = (data) => respond({ id, ok: true, data });
  switch (action) {
    case "log":
      return;
    case "write_external": {
      if (!grants.has("write:external_output")) return err("permission_denied", "write:external_output");
      const name = payload.suggested_filename;
      const b64 = payload.content_base64;
      if (typeof name !== "string" || name === "" || unsafeFilename(name)) {
        return err("bad_request", "Invalid suggested_filename");
      }
      if (typeof b64 !== "string") return err("bad_request", "content_base64 must be a string");
      // Dart's base64Decode is strict: canonical alphabet and padding only.
      if (!/^[A-Za-z0-9+/]*={0,2}$/.test(b64) || b64.length % 4 !== 0) {
        return err("bad_request", "content_base64 is not valid base64");
      }
      const bytes = new Uint8Array(Buffer.from(b64, "base64"));
      if (bytes.length > 50 * 1024 * 1024) return err("bad_request", "File exceeds the 50 MB limit");
      if (saveOutcome === "failed") return err("internal", "Could not write the file");
      if (saveOutcome === "cancelled") return ok({ saved: false });
      savedFiles.push({ name, bytes });
      return ok({ saved: true });
    }
    case "compute": {
      if (typeof payload.function !== "string" || payload.function === "") {
        return err("bad_request", 'compute requires a "function"');
      }
      const prim = (v) => typeof v === "number" || typeof v === "boolean" || typeof v === "string";
      const args = payload.args ?? {};
      for (const v of Object.values(args)) {
        if (!(prim(v) || (Array.isArray(v) && v.every(prim)))) {
          return err("bad_request", "compute args must be JSON primitives or flat arrays (no nested objects)");
        }
      }
      return ok({ echoed: { function: payload.function, args } });
    }
    case "read": {
      const perms = {
        portfolio_names: "read:portfolio_names",
        aggregated_values: "read:aggregated_values",
      };
      const perm = perms[payload.resource];
      if (!perm) return err("bad_request", `Unknown read resource: ${payload.resource}`);
      if (!grants.has(perm)) return err("permission_denied", perm);
      return ok({ resource: payload.resource, args: payload.args });
    }
    default:
      return err("bad_request", `Unknown action: ${action}`);
  }
}

function installAppShim() {
  delete window.HQBridge;
  window.ipc = { postMessage: fakeHost };
  new Function(SHIM)(); // defines window.HQBridge and window.__hqResolve
}

beforeEach(() => {
  posted = [];
  grants = new Set(["write:external_output", "read:portfolio_names"]);
  saveOutcome = "saved";
  savedFiles = [];
  installAppShim();
});

const hosts = [];
/** An HQHost that is disposed after the test (each one adds a listener). */
function newHost() {
  const host = new HQHost();
  hosts.push(host);
  return host;
}
afterEach(() => hosts.splice(0).forEach((h) => h.dispose()));

const lastMessage = () => JSON.parse(posted.at(-1));

// ── write_external ──────────────────────────────────────────────────────────

test("writeExternal sends exactly the host's write_external envelope", async () => {
  const host = newHost();
  const text = "# 家庭会议报告\n\nUSD 1,234.50 — “Read at”\n";
  const result = await host.writeExternal("hellohq-family-report-2026-10-04-zh-Hans.md", text);
  assert.deepEqual(result, { saved: true });
  const msg = lastMessage();
  assert.equal(msg.action, "write_external");
  assert.deepEqual(Object.keys(msg.payload).sort(), ["content_base64", "suggested_filename"]);
  assert.equal(msg.payload.suggested_filename, "hellohq-family-report-2026-10-04-zh-Hans.md");
  // UTF-8, no BOM, byte-for-byte.
  const saved = savedFiles[0];
  assert.deepEqual(Buffer.from(saved.bytes), Buffer.from(text, "utf8"));
  assert.notEqual(saved.bytes[0], 0xef);
});

test("bytes pass through unchanged, including large chunked payloads", async () => {
  const host = newHost();
  const bytes = new Uint8Array(3 * 0x8000 + 7);
  for (let i = 0; i < bytes.length; i++) bytes[i] = (i * 31 + 7) & 0xff;
  bytes[0] = 0x00;
  bytes[1] = 0xff;
  await host.writeExternal("blob.bin", bytes);
  assert.deepEqual(savedFiles[0].bytes, bytes);
  await host.writeExternal("empty.txt", "");
  assert.equal(savedFiles[1].bytes.length, 0);
});

test("a cancelled save dialog resolves saved: false", async () => {
  saveOutcome = "cancelled";
  assert.deepEqual(await newHost().writeExternal("r.md", "x"), { saved: false });
});

test("host errors map to typed errors", async () => {
  grants.delete("write:external_output");
  await assert.rejects(newHost().writeExternal("r.md", "x"), (e) => {
    assert.ok(e instanceof HQPermissionError);
    assert.equal(e.permissionId, "write:external_output");
    return true;
  });
  grants.add("write:external_output");
  saveOutcome = "failed";
  await assert.rejects(newHost().writeExternal("r.md", "x"), (e) => {
    assert.ok(e instanceof HQHostError);
    assert.equal(e.code, "internal");
    return true;
  });
});

const BAD_NAMES = ["", "a/b.md", "a\\b.md", "..", "report..md", "../r.md", "r\u0000.md", "x".repeat(256)];

test("filenames the host would refuse are refused before anything is sent", async () => {
  const host = newHost();
  for (const name of BAD_NAMES) {
    assert.ok(writeExternalFilenameProblem(name), JSON.stringify(name));
    await assert.rejects(host.writeExternal(name, "x"), (e) => e instanceof HQHostError && e.code === "bad_request");
  }
  assert.equal(posted.length, 0);
  // ...and the host agrees: it refuses each of them too.
  for (const name of BAD_NAMES) {
    await assert.rejects(window.HQBridge.writeExternal(name, "eA=="), (e) => e.code === "bad_request");
  }
});

test("filenames the host accepts are accepted", async () => {
  const host = newHost();
  for (const name of ["r.md", "x".repeat(255), "家庭会议报告 2026.html", ".hidden", "a.b.c"]) {
    assert.equal(writeExternalFilenameProblem(name), null, name);
    assert.deepEqual(await host.writeExternal(name, "x"), { saved: true });
  }
});

test("content over 50 MB is refused before it is encoded", async () => {
  assert.equal(MAX_WRITE_EXTERNAL_BYTES, 50 * 1024 * 1024);
  const host = newHost();
  await assert.rejects(
    host.writeExternal("big.bin", new Uint8Array(MAX_WRITE_EXTERNAL_BYTES + 1)),
    (e) => e instanceof HQHostError && e.code === "bad_request",
  );
  assert.equal(posted.length, 0);
});

// ── compute / read through the app shim ─────────────────────────────────────

test("compute sends { function, args } and resolves the host's data", async () => {
  const out = await newHost().compute("report", { lang: "zh-Hans" });
  assert.deepEqual(lastMessage(), {
    id: lastMessage().id,
    action: "compute",
    payload: { function: "report", args: { lang: "zh-Hans" } },
  });
  assert.deepEqual(out, { echoed: { function: "report", args: { lang: "zh-Hans" } } });
});

test("compute args the host rejects surface as bad_request", async () => {
  await assert.rejects(
    newHost().compute("report", { nested: { a: 1 } }),
    (e) => e instanceof HQHostError && e.code === "bad_request",
  );
});

test("reads send the host's { resource, args } shape", async () => {
  const host = newHost();
  await host.readPortfolioNames();
  assert.deepEqual(lastMessage().payload, { resource: "portfolio_names", args: {} });
  grants.add("read:aggregated_values");
  await host.readAggregatedValues("ptf_1");
  assert.deepEqual(lastMessage().payload, { resource: "aggregated_values", args: { portfolio_id: "ptf_1" } });
  grants.delete("read:aggregated_values");
  await assert.rejects(host.readAggregatedValues("ptf_1"), (e) => e instanceof HQPermissionError);
});

// ── legacy postMessage transport and no bridge ──────────────────────────────

test("a postMessage-only bridge still works (legacy transport)", async () => {
  const sent = [];
  window.HQBridge = { postMessage: (raw) => sent.push(JSON.parse(raw)) };
  const host = newHost();
  const pending = host.writeExternal("r.txt", "hi");
  const msg = sent[0];
  assert.equal(msg.action, "write_external");
  assert.deepEqual(msg.payload, { suggested_filename: "r.txt", content_base64: "aGk=" });
  const ev = new Event("message");
  ev.data = JSON.stringify({ id: msg.id, data: { saved: true } });
  window.dispatchEvent(ev);
  assert.deepEqual(await pending, { saved: true });
});

test("without a bridge every call rejects bridge_unavailable", async () => {
  delete window.HQBridge;
  await assert.rejects(newHost().writeExternal("r.md", "x"), (e) => e.code === "bridge_unavailable");
  window.HQBridge = {};
  await assert.rejects(newHost().compute("report", {}), (e) => e.code === "bridge_unavailable");
});
