// SPDX-License-Identifier: Apache-2.0
//
// Vite config for the Allocation Lens WebView UI.
//
// HelloHQ serves the built bundle from a loopback origin under a strict CSP
// (script-src 'self'), so the output MUST be CSP-safe:
//   • base: "./"            — relative asset URLs (the bundle is served from a
//                             nested path, not the origin root).
//   • build.modulePreload:  disabled — Vite's module-preload polyfill emits an
//     false                   INLINE <script> in index.html, which CSP forbids.
// With these, dist/index.html references only external `src=`/module scripts.
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  base: "./",
  build: {
    outDir: "dist",
    modulePreload: false,
    target: "es2022",
  },
});
