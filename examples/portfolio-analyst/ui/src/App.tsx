// SPDX-License-Identifier: Apache-2.0
//
// Portfolio AI Analyst — WebView UI (React).
//
// The compute half is a Tier-1 Python sidecar (../plugin.py). This component
// demonstrates the sidecar bridge from React:
//   1. host.compute("analyse", {})        -> {observations, recommendations, …}
//   2. host.on("analysis-progress", cb)   -> push events the sidecar emits with
//      emit_event between reasoning steps (Tier-1 sidecars drive WebView push
//      events too).

import { useEffect, useRef, useState } from "react";
import { HQHost, HQPermissionError } from "@hellohq/plugin-sdk";

interface Analysis {
  observations: string;
  recommendations: string;
  model: string;
  tokens: number;
}

interface Progress {
  step: string;
  status: string;
}

const STEP_LABELS: Record<string, string> = {
  observations: "Reading the portfolio",
  recommendations: "Drafting suggestions",
};

export function App(): JSX.Element {
  const hostRef = useRef<HQHost>();
  if (!hostRef.current) hostRef.current = new HQHost();
  const host = hostRef.current;

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress[]>([]);

  // Subscribe to the sidecar's progress events for the lifetime of the app.
  useEffect(() => {
    const off = host.on("analysis-progress", (p) => {
      const next = p as Progress;
      setProgress((prev) => [...prev, next]);
    });
    return off;
  }, [host]);

  async function runAnalysis(): Promise<void> {
    setLoading(true);
    setError(null);
    setResult(null);
    setProgress([]);
    try {
      const data = await host.compute<Analysis>("analyse", {});
      setResult(data);
    } catch (e) {
      setError(
        e instanceof HQPermissionError
          ? `Permission denied: ${e.permissionId}`
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app" aria-busy={loading}>
      <h2>Portfolio AI Analyst</h2>
      <p className="lede">
        Generate a plain-language read of your portfolio, powered by your
        configured AI backend.
      </p>

      <button onClick={() => void runAnalysis()} disabled={loading}>
        {loading ? "Analysing…" : "Generate analysis"}
      </button>

      {loading && progress.length > 0 && (
        <div className="progress" role="status">
          <ul>
            {progress.map((p, i) => (
              <li key={i}>
                {STEP_LABELS[p.step] ?? p.step}
                {p.status === "done" ? " — done" : "…"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {result && (
        <>
          {result.observations && (
            <section className="section">
              <h3>Observations</h3>
              <div className="body">{result.observations}</div>
            </section>
          )}
          {result.recommendations && (
            <section className="section">
              <h3>Recommendations</h3>
              <div className="body">{result.recommendations}</div>
            </section>
          )}
          <div className="footer">
            {result.model ? `Model: ${result.model}` : "AI unavailable"}
            {result.tokens > 0 ? ` · ${result.tokens} tokens` : ""}
          </div>
        </>
      )}
    </main>
  );
}
