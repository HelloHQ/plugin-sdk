// SPDX-License-Identifier: Apache-2.0
//
// FX Opportunity Advisor — WebView UI entry point (Vue + Vite).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py). This mounts the
// Vue app; App.vue drives the bridge interactions.

import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";

createApp(App).mount("#app");
