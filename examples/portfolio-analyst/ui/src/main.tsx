// SPDX-License-Identifier: Apache-2.0
//
// Portfolio AI Analyst — WebView UI entry point (React + Vite).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py). This mounts the
// React app; App.tsx drives the bridge interactions.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root")!;
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
