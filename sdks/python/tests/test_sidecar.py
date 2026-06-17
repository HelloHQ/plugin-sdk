"""Tests for the sidecar serve() loop (RPC envelope + lifecycle)."""

from __future__ import annotations

import io
import json

from hellohq_plugin_sdk import PluginError, UnsupportedFunction, serve


def _drive(messages, dispatch):
    """Feed *messages* (list of dicts) through serve() and return output dicts."""
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    out = io.StringIO()
    serve(dispatch, stdin=inp, stdout=out)
    return [json.loads(line) for line in out.getvalue().strip().splitlines()]


def test_ready_then_result() -> None:
    def dispatch(function, args):
        assert function == "echo"
        return {"got": args}

    lines = _drive([{"id": 1, "function": "echo", "args": {"a": 1}}], dispatch)
    assert lines[0]["type"] == "ready"
    assert lines[0]["protocol_version"] == "0.1.0"
    assert lines[1] == {"id": 1, "result": {"got": {"a": 1}}}


def test_plugin_error_envelope() -> None:
    def dispatch(function, args):
        raise PluginError("nope", "invalid_input")

    lines = _drive([{"id": 2, "function": "boom", "args": None}], dispatch)
    assert lines[1]["id"] == 2
    assert lines[1]["error"]["code"] == "invalid_input"
    assert lines[1]["error"]["message"] == "nope"


def test_unsupported_function_envelope() -> None:
    def dispatch(function, args):
        raise UnsupportedFunction(function)

    lines = _drive([{"id": 3, "function": "missing", "args": None}], dispatch)
    assert lines[1]["error"]["code"] == "unsupported_function"


def test_generic_exception_becomes_execution_failed() -> None:
    def dispatch(function, args):
        raise ValueError("kaboom")

    lines = _drive([{"id": 4, "function": "x", "args": None}], dispatch)
    assert lines[1]["error"]["code"] == "execution_failed"
    assert "kaboom" in lines[1]["error"]["message"]


def test_ping_pong() -> None:
    def dispatch(function, args):  # not reached
        return None

    lines = _drive([{"type": "ping", "seq": 9}], dispatch)
    assert lines[1] == {"type": "pong", "seq": 9}


def test_shutdown_stops_loop() -> None:
    calls = []

    def dispatch(function, args):
        calls.append(function)
        return {}

    lines = _drive(
        [
            {"type": "shutdown"},
            {"id": 1, "function": "after_shutdown", "args": None},
        ],
        dispatch,
    )
    # Only `ready` is emitted; the post-shutdown RPC is never dispatched.
    assert lines == [{"type": "ready", "protocol_version": "0.1.0"}]
    assert calls == []


def test_message_without_id_is_ignored() -> None:
    def dispatch(function, args):
        raise AssertionError("should not be called")

    lines = _drive([{"function": "no_id", "args": None}], dispatch)
    assert lines == [{"type": "ready", "protocol_version": "0.1.0"}]
