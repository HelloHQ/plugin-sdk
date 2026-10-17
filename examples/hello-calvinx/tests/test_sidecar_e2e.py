"""End to end: the BUNDLED plugin.py (what ships) as a real sidecar process, driven by a
double of the app's host over the NDJSON protocol (tests/host_double.py), every message
validated against plugin-protocol's sidecar schemas. Nothing is installed: no SDK on
PYTHONPATH, isolated mode (-I), an allowlisted environment.
"""

from __future__ import annotations

import pytest

import bundle
from hello_calvinx import portfolio_names_ui
from host_double import app_context, run_sidecar


@pytest.fixture
def plugin(tmp_path):
    out = tmp_path / "dist" / "plugin.py"
    out.parent.mkdir()
    out.write_text(bundle.build(), encoding="utf-8", newline="\n")
    return out


def _assert_clean(run):
    assert run.exit_code == 0
    assert run.audit == [], f"file/network/process access: {run.audit}"
    # Exactly: ready, the RPC reply, pong. No host call, no event.
    assert [m.get("type", "rpc") for m in run.from_plugin] == ["ready", "rpc", "pong"]
    assert run.from_plugin[0] == {"type": "ready", "protocol_version": "0.1.0"}


def test_lists_the_portfolio_names(plugin, tmp_path):
    names = ["Personal", "Business", "Retirement"]
    run = run_sidecar(plugin, app_context(names), tmp_path)
    _assert_clean(run)
    assert run.reply == {"id": 1, "result": portfolio_names_ui(names)}
    table = run.reply["result"]["children"][2]
    assert [r["name"] for r in table["rows"]] == names


def test_zero_portfolios_shows_the_empty_state(plugin, tmp_path):
    run = run_sidecar(plugin, app_context([]), tmp_path)
    _assert_clean(run)
    assert run.reply["result"]["children"][1]["title"] == "No portfolios yet"


def test_refused_read_shows_unavailable(plugin, tmp_path):
    run = run_sidecar(plugin, app_context(None), tmp_path)
    _assert_clean(run)
    assert run.reply["result"]["children"][1]["title"] == "Portfolio names unavailable"


def test_unknown_input_function_is_a_protocol_error_not_a_crash(plugin, tmp_path):
    run = run_sidecar(plugin, app_context(["A"]), tmp_path, input={"function": "nope", "args": {}})
    _assert_clean(run)
    assert run.reply == {"id": 1, "error": {"code": "unsupported_function", "message": "unsupported function: nope"}}


def test_hostile_names_survive_the_wire(plugin, tmp_path):
    names = ['"quoted"\\', "new\nline", "\u0000ctl", "日本語 🚀", "</script>"]
    run = run_sidecar(plugin, app_context(names), tmp_path)
    _assert_clean(run)
    assert [r["name"] for r in run.reply["result"]["children"][2]["rows"]] == names


def test_negative_control_the_double_catches_file_access_and_host_calls(tmp_path):
    """Guards the guards: a plugin that writes a file, or sends a host call, fails the run."""
    line = '    return portfolio_names_ui(names_from_context(args.get("context")))'
    src = bundle.build()
    assert src.count(line) == 1
    leaky = tmp_path / "leaky.py"
    leaky.write_text(src.replace(line, '    open("probe.txt", "w").close()\n' + line), encoding="utf-8")
    run = run_sidecar(leaky, app_context(["A"]), tmp_path)
    assert any(a.startswith("open ") and "probe.txt" in a for a in run.audit), run.audit

    chatty = tmp_path / "chatty.py"
    host_call = '    sys.stdout.write(\'{"type":"storage_get","seq":0,"key":"k"}\\n\'); sys.stdout.flush()\n'
    chatty.write_text(src.replace(line, host_call + line), encoding="utf-8")
    with pytest.raises(AssertionError, match="unexpected host call"):
        run_sidecar(chatty, app_context(["A"]), tmp_path)
