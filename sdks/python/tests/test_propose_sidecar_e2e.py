"""A real ``serve()`` sidecar process proposing over NDJSON stdin/stdout.

The test plays the host: it reads the sidecar's stdout line by line, answers
each ``propose`` with a ``propose_response``, and sends the RPC request and
shutdown like the real host does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from propose_helpers import batch_wire, valuation_wire

SDK = Path(__file__).resolve().parents[1]

PLUGIN = textwrap.dedent(
    """
    from hellohq_plugin_sdk import PluginError, serve
    from hellohq_plugin_sdk import host
    from hellohq_plugin_sdk.proposals import ProposeError, ProposeUnsupported

    def dispatch(function, args):
        proposals = args["proposals"]
        try:
            receipts = host.propose(proposals)
        except ProposeUnsupported:
            return {"status": "host_unsupported"}
        except ProposeError as exc:
            return {"status": "refused", "code": exc.code, "reason": exc.reason, "retryable": exc.retryable}
        return {"status": "submitted", "receipts": [list(r) for r in receipts]}

    serve(dispatch)
    """
)


def _host_session(tmp_path, proposals, answer, source=PLUGIN):
    """Run the sidecar; ``answer(msg) -> dict`` produces each propose reply. Returns (result, seen)."""
    script = tmp_path / "plugin.py"
    script.write_text(source)
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SDK)},
    )
    seen: list[dict] = []
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["type"] == "ready"
        proc.stdin.write(
            json.dumps({"id": 1, "function": "run", "args": {"proposals": proposals}})
            + "\n"
        )
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            assert line, f"sidecar exited early: {proc.stderr.read()}"
            msg = json.loads(line)
            seen.append(msg)
            if msg.get("type") == "propose":
                proc.stdin.write(json.dumps(answer(msg)) + "\n")
                proc.stdin.flush()
            elif msg.get("id") == 1:
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=10)
                return msg, seen
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


def test_receipts_round_trip_through_a_real_sidecar(tmp_path):
    proposals = [
        valuation_wire(source_key="k:1"),
        valuation_wire(source_key="k:2"),
        valuation_wire(source_key="k:3"),
    ]
    outcomes = [
        {"outcome": "queued"},
        {"outcome": "duplicate"},
        {"outcome": "invalid", "reason": "bad_value"},
    ]

    def answer(msg):
        assert msg["batch"] == batch_wire(*proposals)
        return {
            "type": "propose_response",
            "seq": msg["seq"],
            "receipts": [{"index": i, **o} for i, o in enumerate(outcomes)],
        }

    reply, seen = _host_session(tmp_path, proposals, answer)
    assert reply["result"] == {
        "status": "submitted",
        "receipts": [
            [0, "queued", None],
            [1, "duplicate", None],
            [2, "invalid", "bad_value"],
        ],
    }
    assert [m.get("type") for m in seen] == ["propose", None]


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            {
                "error_code": "rate_limit_exceeded",
                "error": "Too many propose calls; try again later.",
            },
            {
                "status": "refused",
                "code": "rate_limit_exceeded",
                "reason": None,
                "retryable": True,
            },
        ),
        (
            {
                "error_code": "bad_request",
                "reason": "bad_schema",
                "error": "The proposal batch is not valid.",
            },
            {
                "status": "refused",
                "code": "bad_request",
                "reason": "bad_schema",
                "retryable": False,
            },
        ),
        (
            {
                "error_code": "permission_denied",
                "error": "propose permission is not granted for this plugin.",
            },
            {
                "status": "refused",
                "code": "permission_denied",
                "reason": None,
                "retryable": False,
            },
        ),
        (
            {"error_code": "unknown_method", "error": "unknown_method:propose"},
            {"status": "host_unsupported"},
        ),
    ],
)
def test_refusals_reach_the_plugin_as_typed_errors(tmp_path, error, expected):
    def answer(msg):
        return {"type": "propose_response", "seq": msg["seq"], **error}

    reply, _ = _host_session(tmp_path, [valuation_wire()], answer)
    assert reply["result"] == expected


def test_two_calls_use_two_seqs(tmp_path):
    source = PLUGIN.replace(
        "receipts = host.propose(proposals)",
        "host.propose(proposals); receipts = host.propose(proposals)",
    )
    seqs: list[int] = []

    def answer(msg):
        seqs.append(msg["seq"])
        return {
            "type": "propose_response",
            "seq": msg["seq"],
            "receipts": [{"index": 0, "outcome": "queued"}],
        }

    reply, _ = _host_session(tmp_path, [valuation_wire()], answer, source)
    assert reply["result"]["status"] == "submitted"
    assert len(seqs) == 2 and seqs[0] != seqs[1]
