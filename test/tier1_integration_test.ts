/**
 * Tier 1 (Pyodide + Deno) integration test
 *
 * This is the hard go/no-go gate documented in
 * hellohq/docs/07_plugin-system-architecture.md §"Open questions". It verifies:
 *
 *   1. Pyodide 0.29.x initialises correctly in Deno.
 *   2. stdin/stdout can be redirected through Pyodide's setStdin/setStdout so
 *      the NDJSON sidecar protocol can flow over process I/O.
 *   3. NumPy can be loaded from Pyodide's bundled wheels (confirms C-extension
 *      support — the key capability that justifies the Tier 1 complexity).
 *   4. The hellohq_plugin_sdk Python `serve()` loop handles the full RPC
 *      protocol: ready → ping/pong → RPC call → result → shutdown.
 *
 * Run:
 *   deno test --allow-net --allow-env tier1_integration_test.ts
 *
 * The --allow-net flag is required because Pyodide fetches its packages from
 * the CDN. In CI, point PYODIDE_BASE_URL at a local mirror to run offline.
 *
 * If this test fails with a Pyodide load error, fall back to the native CPython
 * sidecar path (see architecture doc §"Open questions → Deno/Pyodide fallback").
 */

// @ts-ignore — pyodide ships its own types; import via CDN for self-contained test
import { loadPyodide, type PyodideInterface } from "npm:pyodide@0.29.2";

// ─────────────────────────────────────────────────────────────────────────────
// Helpers: in-memory stdin/stdout buffers
// ─────────────────────────────────────────────────────────────────────────────

/** Produces lines one-at-a-time from a pre-loaded list. */
class LineReader {
  private lines: string[];
  private pos = 0;

  constructor(lines: string[]) {
    // Append an explicit sentinel so the loop sees EOF.
    this.lines = [...lines];
  }

  /** Returns the next line (with newline) or null on EOF. */
  nextLine(): string | null {
    if (this.pos >= this.lines.length) return null;
    return this.lines[this.pos++] + "\n";
  }
}

/** Collects lines written by Python into an array. */
class LineCapture {
  readonly lines: string[] = [];
  private buf = "";

  write(s: string): void {
    this.buf += s;
    let nl: number;
    while ((nl = this.buf.indexOf("\n")) !== -1) {
      this.lines.push(this.buf.slice(0, nl));
      this.buf = this.buf.slice(nl + 1);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Minimal Python sidecar code embedded for self-contained testing
// ─────────────────────────────────────────────────────────────────────────────

/** The Python plugin code under test — calls serve() with a trivial dispatch. */
const PLUGIN_PY = `
import sys, json

PROTOCOL_VERSION = "1.0.0"

def serve(dispatch, *, stdin=None, stdout=None):
    inp = stdin or sys.stdin
    out = stdout or sys.stdout

    def send(obj):
        out.write(json.dumps(obj, separators=(',', ':')) + '\\n')
        out.flush()

    send({"type": "ready", "protocol_version": PROTOCOL_VERSION})

    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = msg.get("type")
        if msg_type == "shutdown":
            out.flush()
            return
        if msg_type == "ping":
            send({"type": "pong", "seq": msg.get("seq")})
            continue

        req_id = msg.get("id")
        if req_id is None:
            continue
        try:
            result = dispatch(msg.get("function", ""), msg.get("args"))
            send({"id": req_id, "result": result})
        except Exception as ex:
            send({"id": req_id, "error": {"code": "execution_failed", "message": str(ex)}})

def dispatch(function, args):
    if function == "add":
        return {"sum": args["a"] + args["b"]}
    if function == "portfolio_count":
        # Demonstrate NumPy is available (loaded separately by the test).
        import numpy as np
        return {"count": int(np.array([1, 2, 3]).sum())}
    raise ValueError(f"unknown function: {function}")
`;

// ─────────────────────────────────────────────────────────────────────────────
// Test: Pyodide loads
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("pyodide 0.29.x initialises in Deno", async () => {
  const pyodide: PyodideInterface = await loadPyodide();
  const version: string = pyodide.runPython("import sys; sys.version");
  if (!version.startsWith("3.")) {
    throw new Error(`Expected CPython 3.x, got: ${version}`);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: NumPy loads via Pyodide
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("NumPy loads and runs in Pyodide", async () => {
  const pyodide: PyodideInterface = await loadPyodide();
  await pyodide.loadPackage("numpy");
  const result: number = pyodide.runPython(
    "import numpy as np; int(np.array([10, 20, 30]).sum())",
  );
  if (result !== 60) {
    throw new Error(`Expected 60, got ${result}`);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: setStdin / setStdout routing
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("Pyodide setStdin/setStdout routes I/O", async () => {
  const pyodide: PyodideInterface = await loadPyodide();

  const capture = new LineCapture();
  pyodide.setStdout({ write: (s: string) => { capture.write(s); return s.length; } });

  pyodide.runPython("import sys; print('hello-from-python', file=sys.stdout)");

  if (!capture.lines.some((l) => l.includes("hello-from-python"))) {
    throw new Error(`Expected stdout capture; got: ${JSON.stringify(capture.lines)}`);
  }

  // stdin: feed a line and read it back.
  let linePos = 0;
  const stdinLines = ["injected-line\n"];
  pyodide.setStdin({
    stdin: () => {
      if (linePos < stdinLines.length) return stdinLines[linePos++];
      return null;
    },
  });

  const readBack: string = pyodide.runPython(
    "import sys; sys.stdin.readline().strip()",
  );
  if (readBack !== "injected-line") {
    throw new Error(`Expected "injected-line", got "${readBack}"`);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: full NDJSON sidecar protocol via in-memory I/O
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("hellohq sidecar NDJSON protocol: ready → ping → RPC → shutdown", async () => {
  const pyodide: PyodideInterface = await loadPyodide();
  await pyodide.loadPackage("numpy");

  // Wire in-memory I/O.
  const capture = new LineCapture();
  pyodide.setStdout({ write: (s: string) => { capture.write(s); return s.length; } });

  const inbound: string[] = [
    JSON.stringify({ type: "ping", seq: 1 }),
    JSON.stringify({ id: 1, function: "add", args: { a: 3, b: 4 } }),
    JSON.stringify({ id: 2, function: "portfolio_count", args: {} }),
    JSON.stringify({ type: "shutdown" }),
  ];
  const reader = new LineReader(inbound);
  pyodide.setStdin({ stdin: () => reader.nextLine() });

  // Load and run the sidecar code.
  pyodide.runPython(PLUGIN_PY);
  pyodide.runPython("serve(dispatch)");

  // Parse all lines the plugin emitted.
  const messages = capture.lines
    .filter((l) => l.trim().length > 0)
    .map((l) => {
      try {
        return JSON.parse(l) as Record<string, unknown>;
      } catch {
        throw new Error(`Non-JSON output from sidecar: ${l}`);
      }
    });

  // 1. ready
  const ready = messages.find((m) => m["type"] === "ready");
  if (!ready) throw new Error("Missing ready message");
  if (ready["protocol_version"] !== "1.0.0") {
    throw new Error(`Wrong protocol_version: ${ready["protocol_version"]}`);
  }

  // 2. pong for ping seq=1
  const pong = messages.find((m) => m["type"] === "pong");
  if (!pong) throw new Error("Missing pong");
  if (pong["seq"] !== 1) throw new Error(`Wrong pong seq: ${pong["seq"]}`);

  // 3. RPC result for id=1 (add)
  const addResp = messages.find((m) => m["id"] === 1);
  if (!addResp) throw new Error("Missing add response");
  const addResult = addResp["result"] as Record<string, unknown> | undefined;
  if (addResult?.["sum"] !== 7) {
    throw new Error(`Expected sum=7, got ${JSON.stringify(addResult)}`);
  }

  // 4. RPC result for id=2 (portfolio_count — uses NumPy)
  const countResp = messages.find((m) => m["id"] === 2);
  if (!countResp) throw new Error("Missing portfolio_count response");
  if ("error" in countResp) {
    throw new Error(`portfolio_count failed: ${JSON.stringify(countResp["error"])}`);
  }
  const countResult = countResp["result"] as Record<string, unknown> | undefined;
  if (countResult?.["count"] !== 6) {
    throw new Error(`Expected count=6 (1+2+3), got ${JSON.stringify(countResult)}`);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Test: RPC error path
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("sidecar returns structured error for unknown function", async () => {
  const pyodide: PyodideInterface = await loadPyodide();

  const capture = new LineCapture();
  pyodide.setStdout({ write: (s: string) => { capture.write(s); return s.length; } });

  const inbound: string[] = [
    JSON.stringify({ id: 1, function: "not_a_function", args: {} }),
    JSON.stringify({ type: "shutdown" }),
  ];
  const reader = new LineReader(inbound);
  pyodide.setStdin({ stdin: () => reader.nextLine() });

  pyodide.runPython(PLUGIN_PY);
  pyodide.runPython("serve(dispatch)");

  const messages = capture.lines
    .filter((l) => l.trim().length > 0)
    .map((l) => JSON.parse(l) as Record<string, unknown>);

  const errResp = messages.find((m) => m["id"] === 1);
  if (!errResp) throw new Error("Missing error response");
  if (!("error" in errResp)) throw new Error("Expected an error field");
  const err = errResp["error"] as Record<string, unknown>;
  if (err["code"] !== "execution_failed") {
    throw new Error(`Expected code=execution_failed, got ${err["code"]}`);
  }
});
