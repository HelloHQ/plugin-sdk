<!--
  SPDX-License-Identifier: Apache-2.0

  FX Opportunity Advisor — WebView UI (Vue).

  The compute half is a Tier-1 Python sidecar (../plugin.py). This component
  demonstrates the sidecar bridge from Vue:
    host.compute("analyse", {}) -> { headline, strong, weak, recommendation, model, tokens }
-->
<script setup lang="ts">
import { onUnmounted, ref } from "vue";
import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

interface Analysis {
  headline: string;
  strong: string[];
  weak: string[];
  recommendation: string;
  model: string;
  tokens: number;
}

const host = new HQHost();
onUnmounted(() => host.dispose());

const loading = ref(false);
const result = ref<Analysis | null>(null);
const error = ref<string | null>(null);

async function runAnalysis(): Promise<void> {
  loading.value = true;
  error.value = null;
  result.value = null;
  try {
    result.value = await host.compute<Analysis>("analyse", {});
  } catch (e) {
    error.value =
      e instanceof HQPermissionError
        ? `Permission denied: ${e.permissionId}`
        : e instanceof Error
          ? e.message
          : String(e);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="app" :aria-busy="loading">
    <h2>FX Opportunity Advisor</h2>
    <p class="lede">
      Read the workspace currency rates and ask your configured AI backend which
      currencies look strong or weak against USD.
    </p>

    <button :disabled="loading" @click="runAnalysis">
      {{ loading ? "Analysing…" : "Analyse FX" }}
    </button>

    <div v-if="error" class="error">{{ error }}</div>

    <template v-if="result">
      <p v-if="result.headline" class="headline">{{ result.headline }}</p>

      <section class="section">
        <h3>Strong vs USD</h3>
        <div v-if="result.strong.length" class="badge-row">
          <span v-for="c in result.strong" :key="c" class="badge strong">
            {{ c.toUpperCase() }}
          </span>
        </div>
        <p v-else class="muted">No strong signals.</p>
      </section>

      <section class="section">
        <h3>Weak vs USD</h3>
        <div v-if="result.weak.length" class="badge-row">
          <span v-for="c in result.weak" :key="c" class="badge weak">
            {{ c.toUpperCase() }}
          </span>
        </div>
        <p v-else class="muted">No weak signals.</p>
      </section>

      <section v-if="result.recommendation" class="section">
        <h3>Portfolio Implication</h3>
        <div class="body">{{ result.recommendation }}</div>
      </section>

      <div class="footer">
        {{ result.model ? `Model: ${result.model}` : "AI unavailable" }}
        {{ result.tokens > 0 ? ` · ${result.tokens} tokens` : "" }}
      </div>
    </template>
  </main>
</template>
