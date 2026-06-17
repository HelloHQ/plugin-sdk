import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// HelloHQ serves the WebView bundle from a loopback origin under a strict CSP
// (script-src 'self'). So:
//   - base "./"            -> all asset URLs are relative, no absolute origin.
//   - modulePreload: false -> Vite would otherwise inject an inline preload
//                             polyfill <script>, which the CSP forbids.
// The result is an index.html that references only external module scripts.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    modulePreload: false,
  },
});
