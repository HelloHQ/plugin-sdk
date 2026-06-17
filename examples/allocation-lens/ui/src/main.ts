// SPDX-License-Identifier: Apache-2.0
//
// Allocation Lens — WebView UI entry point (Svelte 5).
import { mount } from "svelte";
import App from "./App.svelte";
import "./styles.css";

const app = mount(App, { target: document.getElementById("app")! });

export default app;
