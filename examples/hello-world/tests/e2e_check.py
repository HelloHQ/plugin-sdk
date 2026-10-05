#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end check of the built Hello World component.

Runs plugin.wasm through `hqplugin test` (the CLI's in-process Wasmtime
component host + mock workspace) and asserts on the declarative UI it returns:

  * with the portfolios in tests/fixtures/portfolios.json and the
    read:portfolio_names grant, the table lists exactly those names, in order;
  * with tests/fixtures/no-portfolios.json, an empty state and no table;
  * without the grant, the "unavailable" empty state (the run still succeeds).

Optionally (--wit) also asserts the component's import set: exactly
hellohq:plugin/{types,workspace,log} and the guest export, nothing else.

Stdlib only. Run from the plugin-sdk repo root:

    python3 examples/hello-world/tests/e2e_check.py --wasm examples/hello-world/plugin.wasm
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO = HERE.parents[2]
CLI = REPO / "cli" / "bin" / "hqplugin.dart"

EXPECTED_IMPORTS = {
    "hellohq:plugin/types@0.1.0",
    "hellohq:plugin/workspace@0.1.0",
    "hellohq:plugin/log@0.1.0",
}
EXPECTED_EXPORTS = {"hellohq:plugin/guest@0.1.0"}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def run_cli(dart: str, wasm: Path, fixture: Path | None, grants: list[str]) -> dict:
    cmd = [dart, "run", str(CLI), "test", "--wasm", str(wasm)]
    if fixture is not None:
        cmd += ["--fixture", str(fixture)]
    for g in grants:
        cmd += ["--grant", g]
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        fail(f"{' '.join(cmd)} exited {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    # stdout: a "test: …" header line, a section marker, then the indented
    # JSON document. Decode the first object that starts a line.
    m = re.search(r"^\{", proc.stdout, re.MULTILINE)
    if not m:
        fail(f"no JSON document in output:\n{proc.stdout}")
    doc, _ = json.JSONDecoder().raw_decode(proc.stdout[m.start() :])
    if not isinstance(doc, dict):
        fail(f"top level is not a component object: {doc!r}")
    return doc


def children(doc: dict) -> list:
    if doc.get("type") != "column" or not isinstance(doc.get("children"), list):
        fail(f"root must be a column with children: {doc!r}")
    return doc["children"]


def check_heading(kids: list) -> None:
    if kids[0] != {"type": "heading", "level": 1, "text": "Hello, World!"}:
        fail(f"unexpected heading: {kids[0]!r}")


def check_lists_names(dart: str, wasm: Path) -> None:
    fixture = FIXTURES / "portfolios.json"
    expected = [p["name"] for p in json.loads(fixture.read_text("utf-8"))["portfolios"]]
    kids = children(run_cli(dart, wasm, fixture, ["read:portfolio_names"]))
    check_heading(kids)
    tables = [k for k in kids if k.get("type") == "table"]
    if len(tables) != 1:
        fail(f"expected exactly one table, got {kids!r}")
    table = tables[0]
    if table.get("columns") != [{"key": "name", "label": "Portfolio"}]:
        fail(f"unexpected columns: {table.get('columns')!r}")
    got = [row.get("name") for row in table.get("rows", [])]
    if got != expected:
        fail(f"table rows {got!r} != fixture names {expected!r}")
    summary = f"You have {len(expected)} portfolios in this workspace."
    if not any(k.get("type") == "text" and k.get("content") == summary for k in kids):
        fail(f"missing summary {summary!r}: {kids!r}")
    if any(k.get("type") == "empty-state" for k in kids):
        fail("empty state shown alongside the list")
    print(f"ok  lists exactly the {len(expected)} fixture names")


def check_empty(dart: str, wasm: Path) -> None:
    kids = children(
        run_cli(dart, wasm, FIXTURES / "no-portfolios.json", ["read:portfolio_names"])
    )
    check_heading(kids)
    if any(k.get("type") == "table" for k in kids):
        fail(f"table shown for an empty workspace: {kids!r}")
    empties = [k for k in kids if k.get("type") == "empty-state"]
    if len(empties) != 1 or empties[0].get("title") != "No portfolios yet":
        fail(f"expected the 'No portfolios yet' empty state: {kids!r}")
    print("ok  empty workspace -> empty state")


def check_denied(dart: str, wasm: Path) -> None:
    kids = children(run_cli(dart, wasm, FIXTURES / "portfolios.json", []))
    check_heading(kids)
    if any(k.get("type") == "table" for k in kids):
        fail(f"names listed without the grant: {kids!r}")
    empties = [k for k in kids if k.get("type") == "empty-state"]
    if len(empties) != 1 or empties[0].get("title") != "Portfolio names unavailable":
        fail(f"expected the 'unavailable' empty state: {kids!r}")
    print("ok  no grant -> 'unavailable' empty state")


def check_wit(wasm_tools: str, wasm: Path) -> None:
    proc = subprocess.run(
        [wasm_tools, "component", "wit", str(wasm)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        fail(f"wasm-tools component wit failed:\n{proc.stderr}")
    world = re.search(r"^world root \{(.*?)^\}", proc.stdout, re.MULTILINE | re.DOTALL)
    if not world:
        fail(f"no root world in WIT:\n{proc.stdout}")
    imports = set(re.findall(r"^\s*import\s+([^;\s]+);", world.group(1), re.MULTILINE))
    exports = set(re.findall(r"^\s*export\s+([^;\s]+);", world.group(1), re.MULTILINE))
    if imports != EXPECTED_IMPORTS:
        fail(f"imports {sorted(imports)} != {sorted(EXPECTED_IMPORTS)}")
    if exports != EXPECTED_EXPORTS:
        fail(f"exports {sorted(exports)} != {sorted(EXPECTED_EXPORTS)}")
    print("ok  imports exactly hellohq:plugin/{types,workspace,log}; exports guest")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wasm", required=True, type=Path)
    ap.add_argument("--dart", default="dart", help="dart executable (default: dart)")
    ap.add_argument("--wit", action="store_true", help="also assert the WIT import set")
    ap.add_argument("--wasm-tools", default="wasm-tools")
    ap.add_argument("--skip-run", action="store_true", help="only the --wit check")
    args = ap.parse_args()

    wasm = args.wasm.resolve()
    if not wasm.is_file():
        fail(f"no such file: {wasm}")
    if args.wit:
        check_wit(args.wasm_tools, wasm)
    if not args.skip_run:
        check_lists_names(args.dart, wasm)
        check_empty(args.dart, wasm)
        check_denied(args.dart, wasm)


if __name__ == "__main__":
    main()
