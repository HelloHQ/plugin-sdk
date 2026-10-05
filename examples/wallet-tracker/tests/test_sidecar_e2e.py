"""End to end over the NDJSON protocol: the BUNDLED plugin.py (what ships) as a real
sidecar process, the real SDK, and a fake host that answers ``http_request`` and
``propose`` the way hellohq does (``plugin-protocol`` ``host-calls.schema.json``).

The test plays the host: it sends the ``run`` RPC, serves the plugin's host calls from
its stdout, and reads the result. No network: the fake host serves the fetches.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import bundle
from fixtures import BTC_P2WPKH, address_response

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT.parent.parent / "sdks" / "python"
RUN_ARGS = {"btc_addresses": [BTC_P2WPKH], "price_currency": None}  # one fetch: no price call, no pacing sleep


def _run_sidecar(tmp_path, answer_propose, *, args=RUN_ARGS, fetch=None):
    """Run the bundle; returns ``(rpc_reply, [host calls seen])``.

    ``answer_propose(msg) -> dict`` builds each ``propose_response`` (``type`` and ``seq`` are added).
    """
    plugin = tmp_path / "plugin.py"
    plugin.write_text(bundle.build(ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-u", str(plugin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SDK)},
    )
    seen: list[dict] = []

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        assert json.loads(proc.stdout.readline())["type"] == "ready"
        send({"id": 1, "function": "run", "args": {"context": {}, "input": {"function": "scan", "args": args}}})
        while True:
            line = proc.stdout.readline()
            assert line, f"sidecar exited early: {proc.stderr.read()}"
            msg = json.loads(line)
            if msg.get("type") == "http_request":
                seen.append(msg)
                body = (fetch or (lambda m: address_response(BTC_P2WPKH)))(msg)
                send({"type": "http_response", "seq": msg["seq"], "status": 200, "headers": {}, "body": body})
            elif msg.get("type") == "propose":
                seen.append(msg)
                send({"type": "propose_response", "seq": msg["seq"], **answer_propose(msg)})
            elif msg.get("id") == 1:
                send({"type": "shutdown"})
                proc.wait(timeout=10)
                return msg, seen
            else:
                raise AssertionError(f"unexpected message from the plugin: {msg}")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


def _queued(msg):
    return {"receipts": [{"index": i, "outcome": "queued"} for i, _ in enumerate(msg["batch"]["proposals"])]}


def test_scan_proposes_over_ndjson_and_reports_the_receipts(tmp_path):
    reply, seen = _run_sidecar(tmp_path, _queued)
    report = reply["result"]
    (propose,) = [m for m in seen if m["type"] == "propose"]
    assert propose["batch"]["schema"] == "hellohq.proposal-batch@1"
    (proposal,) = propose["batch"]["proposals"]
    assert proposal["source_key"] == f"btc:address:{BTC_P2WPKH}"
    assert proposal["quantity"] == {"amount": "0.51230000", "unit": "BTC"}
    assert report["submission"]["status"] == "submitted"
    assert report["submission"]["receipts"][0]["outcome"] == "queued"
    assert report["submission"]["summary"] == {"queued": 1}
    assert report["proposals"][0]["proposals"] == [proposal]  # the report keeps what it sent


def test_every_receipt_outcome_round_trips(tmp_path):
    for outcome in ("duplicate", "superseded_older", "unchanged", "suppressed"):
        reply, _ = _run_sidecar(tmp_path, lambda m, o=outcome: {"receipts": [{"index": 0, "outcome": o}]})
        assert reply["result"]["submission"]["summary"] == {outcome: 1}
    reply, _ = _run_sidecar(
        tmp_path, lambda m: {"receipts": [{"index": 0, "outcome": "invalid", "reason": "bad_quantity"}]}
    )
    report = reply["result"]
    assert report["submission"]["receipts"][0]["reason"] == "bad_quantity"
    assert report["issues"][0]["code"] == "proposal_invalid"


@pytest.mark.parametrize(
    "error_code,status,retryable",
    [
        ("permission_denied", "permission_denied", False),
        ("rate_limit_exceeded", "failed", True),
        ("quota_exceeded", "failed", True),
        ("too_large", "failed", False),
        ("workspace_unavailable", "failed", True),
        ("host_error", "failed", False),
    ],
)
def test_refusals_degrade_without_failing_the_run(tmp_path, error_code, status, retryable):
    reply, _ = _run_sidecar(tmp_path, lambda m: {"error": "Fixed text.", "error_code": error_code})
    assert "error" not in reply, reply  # the scan still returns its report, balances and proposals
    report = reply["result"]
    assert report["balances"] and report["proposals"]
    sub = report["submission"]
    assert (sub["status"], sub["code"], sub["retryable"]) == (status, error_code, retryable)


def test_a_host_that_answers_unknown_method_gets_the_host_unsupported_fallback(tmp_path):
    reply, _ = _run_sidecar(tmp_path, lambda m: {"error": "unknown_method:propose", "error_code": "unknown_method"})
    report = reply["result"]
    assert report["submission"]["status"] == "host_unsupported"
    assert report["proposals"] and report["submission"]["receipts"] == []


def test_submit_false_sends_no_propose(tmp_path):
    reply, seen = _run_sidecar(tmp_path, _queued, args={**RUN_ARGS, "submit": False})
    assert not [m for m in seen if m["type"] == "propose"]
    assert reply["result"]["submission"]["status"] == "not_requested"


def test_the_fake_host_replies_conform_to_the_protocol_schema():
    """Guards the test's own fake host: its replies are valid ``propose_response`` messages."""
    schema = _protocol_schema()
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(schema)
    for reply in (
        {"type": "propose_response", "seq": 0, "receipts": [{"index": 0, "outcome": "queued"}]},
        {
            "type": "propose_response",
            "seq": 0,
            "receipts": [{"index": 0, "outcome": "invalid", "reason": "bad_quantity"}],
        },
        {"type": "propose_response", "seq": 0, "error": "Fixed text.", "error_code": "rate_limit_exceeded"},
    ):
        assert validator.is_valid(reply), reply


def _protocol_schema():
    env = os.environ.get("HELLOHQ_PLUGIN_PROTOCOL_DIR")
    for root in ([Path(env)] if env else []) + [
        ROOT.parents[2] / "plugin-protocol",
        ROOT.parents[3] / "plugin-protocol",
    ]:
        path = root / "sidecar" / "host-calls.schema.json"
        if path.is_file():
            return json.loads(path.read_text())
    if os.environ.get("HELLOHQ_REQUIRE_PROTOCOL") == "1":
        pytest.fail("plugin-protocol checkout not found (set HELLOHQ_PLUGIN_PROTOCOL_DIR)")
    pytest.skip("plugin-protocol checkout not found (set HELLOHQ_PLUGIN_PROTOCOL_DIR)")
