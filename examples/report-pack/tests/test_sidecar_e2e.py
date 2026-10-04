"""End-to-end: run ``plugin.py`` as the real sidecar process.

The host side is played by this test using the exact wire format the app and
``hqplugin test --sidecar`` speak (see ``hellohq_plugin_sdk.sidecar.serve``):

    plugin -> host   {"type": "ready", "protocol_version": "0.1.0"}
    host   -> plugin {"id": 1, "function": "run",
                      "args": {"context": {...}, "input": {"function": ..., "args": {...}}}}
    plugin -> host   {"id": 1, "result": ...} | {"id": 1, "error": {...}}
    host   -> plugin {"type": "shutdown"}

``context`` is built the way ``PluginSidecarBridge.snapshot`` does, with the
payload shapes of ``PluginDataAccessObject`` (see ``fixtures.py``).
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

import fixtures as fx
import pytest
from conftest import EXAMPLE_DIR, SDK_DIR
from hellohq_plugin_sdk import serve

import plugin

PLUGIN = EXAMPLE_DIR / "plugin.py"


def run_sidecar(context, input_, *, calls: int = 1) -> list[dict]:
    """Spawn the sidecar, send ``calls`` identical RPCs, return the replies."""
    env = {**os.environ, "PYTHONPATH": str(SDK_DIR), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, str(PLUGIN)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    replies: list[dict] = []
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready == {"type": "ready", "protocol_version": "0.1.0"}
        for rid in range(1, calls + 1):
            request = {
                "id": rid,
                "function": "run",
                "args": {"context": context, "input": input_},
            }
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            replies.append(json.loads(proc.stdout.readline()))
        proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=10)
        assert proc.returncode == 0, proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
        proc.stdin.close()
    return replies


@pytest.mark.parametrize("lang", ["en", "zh-Hans"])
def test_sidecar_process_produces_the_report(lang: str) -> None:
    (reply,) = run_sidecar(fx.FAMILY, {"function": "report", "args": {"lang": lang}})
    assert reply["id"] == 1 and "error" not in reply
    result = reply["result"]
    assert result["lang"] == lang and result["data_status"] == "partial"
    # The clock is real here: compare everything except the timestamp.
    expected = plugin.generate(fx.FAMILY, lang, now=fx.NOW)
    stamp = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\dZ)?")
    for fmt, document in result["documents"].items():
        got = stamp.sub("T", document["content"])
        want = stamp.sub("T", expected["documents"][fmt]["content"])
        assert got == want, fmt
        assert stamp.search(document["content"])
    # Non-ASCII survives the NDJSON pipe intact.
    if lang == "zh-Hans":
        assert "家庭会议报告" in result["documents"]["markdown"]["content"]


def test_withheld_amounts_never_cross_the_pipe() -> None:
    # The mortgaged home's SGD 1,250,000 / CNY 480,000.5 and the unclassified
    # business's USD 48,000 must not reach the WebView in any form.
    (reply,) = run_sidecar(fx.FAMILY, {"function": "report", "args": {"lang": "en"}})
    wire = json.dumps(reply)
    for amount in ("1250000", "1,250,000", "480000", "480,000", "48000.0", "48,000"):
        assert amount not in wire, amount
    assert fx.SGD not in wire and fx.USD not in wire  # ids never shown, only codes


def test_sidecar_handles_a_denied_context_and_repeat_calls() -> None:
    # Both reads denied -> the host omits them. Two calls: the loop stays alive.
    first, second = run_sidecar({}, {"function": "report", "args": {}}, calls=2)
    for reply in (first, second):
        assert reply["result"]["data_status"] == "no_portfolios"
        assert reply["result"]["lang"] == "en"


def test_sidecar_run_alias_and_default_function() -> None:
    (a,) = run_sidecar(fx.NO_TOTALS, {"function": "run", "args": {"lang": "en"}})
    (b,) = run_sidecar(fx.NO_TOTALS, {})
    assert a["result"]["data_status"] == b["result"]["data_status"] == "no_totals"


def test_sidecar_error_envelopes() -> None:
    (bad_fn,) = run_sidecar(fx.FAMILY, {"function": "delete_everything", "args": {}})
    assert bad_fn["error"]["code"] == "unsupported_function"
    (bad_lang,) = run_sidecar(
        fx.FAMILY, {"function": "report", "args": {"lang": "zh-TW"}}
    )
    assert bad_lang["error"]["code"] == "invalid_input"


def test_dispatch_through_the_sdk_serve_loop_in_process() -> None:
    request = {
        "id": 7,
        "function": "run",
        "args": {
            "context": fx.FAMILY,
            "input": {"function": "report", "args": {"lang": "en"}},
        },
    }
    out = io.StringIO()
    serve(plugin.dispatch, stdin=io.StringIO(json.dumps(request) + "\n"), stdout=out)
    ready, reply = (json.loads(line) for line in out.getvalue().splitlines())
    assert ready["type"] == "ready" and reply["id"] == 7
    assert reply["result"]["documents"]["markdown"]["filename"].endswith("-en.md")


def test_bridge_argument_rules_hold_for_the_ui_call() -> None:
    # The host bridge rejects compute args that are not strings, numbers,
    # booleans or flat arrays of them (null included); the UI sends only the
    # language string. The bridge then hands the sidecar
    # input = {"function": fn, "args": args} under args["input"].
    ui_args = {"lang": "zh-Hans"}
    assert all(isinstance(v, str) for v in ui_args.values())
    (reply,) = run_sidecar(fx.FAMILY, {"function": "report", "args": ui_args})
    assert reply["result"]["lang"] == "zh-Hans"
