// SPDX-License-Identifier: Apache-2.0
//
// HelloHQ Tier-2 Component Model quickstart (JS/TS).
//
// The JS twin of `examples/component-quickstart` (Rust). A small headless
// plugin built with `@hellohq/plugin-sdk/component` against the canonical
// `hellohq:plugin@0.1.0` WIT. On `run` it:
//
//   1. logs a banner (`hq.log`),
//   2. reads the workspace portfolio names (`hq.workspace`),
//   3. stores + reads back a value (`hq.storage`),
//   4. emits an event (`hq.events`),
//   5. returns a compact ASCII summary "<n-portfolios>|<roundtrip-ok>" (e.g. "3|1").
//
// It deliberately touches only workspace / storage / events / log, matching the
// Rust quickstart. (The build world `hellohq-plugin-component` already omits
// `inference` — see the SDK's `wit/README.md` for the componentize-js stream
// blocker.)

import { hq, definePlugin, isApiError } from "@hellohq/plugin-sdk/component";

const enc = new TextEncoder();

export const guest = definePlugin({
  init() {
    hq.log.info("component-quickstart-js: init");
  },

  run(_input: Uint8Array): Uint8Array {
    hq.log.debug("component-quickstart-js: run start");

    // 1. Read workspace portfolio names (permission-gated; throws ApiError on
    //    denial). CATCH it and RETURN a degraded summary — an uncaught throw
    //    would trap the guest, not map to the WIT Err (see the SDK error model).
    let names;
    try {
      names = hq.workspace.readPortfolioNames();
    } catch (e) {
      const msg = isApiError(e) ? e.message : String(e);
      hq.log.warn(`workspace read denied: ${msg}`);
      // "denied:<message>" — distinct from a granted "<n>|<ok>" summary, and
      // nothing downstream (storage/events) runs.
      return enc.encode(`denied:${msg}`);
    }
    hq.log.info(`read ${names.length} portfolio name(s)`);

    // 2. Storage round-trip: set "greeting" -> read it back.
    hq.storage.set("greeting", enc.encode("hello"));
    const got = hq.storage.get("greeting");
    const roundtrip =
      got !== undefined && new TextDecoder().decode(got) === "hello";

    // 3. Emit an event (best-effort; swallow a cap/denial here).
    try {
      hq.events.emit("quickstart-ran", enc.encode("ok"));
    } catch {
      // size/rate cap or denial — non-fatal for the quickstart.
    }

    // 4. Compact summary the host can assert: "<n>|<ok>", e.g. "3|1".
    const summary = `${names.length}|${roundtrip ? 1 : 0}`;
    hq.log.debug("component-quickstart-js: run done");
    return enc.encode(summary);
  },

  metadata() {
    return { id: "component-quickstart-js", version: "0.1.0" };
  },
});
