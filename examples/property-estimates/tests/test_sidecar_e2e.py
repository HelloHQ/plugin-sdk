"""The BUNDLED plugin.py as a real sidecar over NDJSON, with the real SDK and a fake host.

The test plays the host: it sends the ``run`` RPC, answers ``http_request`` with a canned
data.gov.sg page and ``propose`` the way hellohq does, then reads the result.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures import sg_page, sg_record

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT.parents[1] / "sdks" / "python"
sys.path.insert(0, str(ROOT))
import bundle  # noqa: E402

PAGE = sg_page([sg_record("2026-08", 500_000 + i * 1000, area="90") for i in range(40)])
ARGS = {"region": "sg_hdb", "town": "BISHAN", "flat_type": "4 ROOM"}


def _run(tmp_path, answer, function="propose"):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(bundle.build())
    proc = subprocess.Popen(
        [sys.executable, "-u", str(plugin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SDK)},
    )
    seen = []

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        assert json.loads(proc.stdout.readline())["type"] == "ready"
        send(
            {
                "id": 1,
                "function": "run",
                "args": {"context": {}, "input": {"function": function, "args": ARGS}},
            }
        )
        while True:
            line = proc.stdout.readline()
            assert line, f"sidecar exited early: {proc.stderr.read()}"
            msg = json.loads(line)
            if msg.get("type") == "http_request":
                seen.append(msg)
                send(
                    {
                        "type": "http_response",
                        "seq": msg["seq"],
                        "status": 200,
                        "headers": {},
                        "body": PAGE,
                    }
                )
            elif msg.get("type") == "propose":
                seen.append(msg)
                send({"type": "propose_response", "seq": msg["seq"], **answer(msg)})
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


def test_proposal_round_trips_over_ndjson(tmp_path):
    reply, seen = _run(
        tmp_path, lambda m: {"receipts": [{"index": 0, "outcome": "superseded_older"}]}
    )
    out = reply["result"]
    (propose,) = [m for m in seen if m["type"] == "propose"]
    assert propose["batch"]["schema"] == "hellohq.proposal-batch@1"
    (proposal,) = propose["batch"]["proposals"]
    assert proposal["kind"] == "valuation" and proposal["source"]["origin"] == "data.gov.sg"
    assert out["proposal"] == proposal
    assert out["submission"] == {
        "status": "submitted",
        "receipts": [{"index": 0, "outcome": "superseded_older"}],
    }


def test_unknown_method_returns_the_proposal_as_data(tmp_path):
    reply, _ = _run(
        tmp_path, lambda m: {"error": "unknown_method:propose", "error_code": "unknown_method"}
    )
    out = reply["result"]
    assert out["submission"]["status"] == "pending_host_support"
    assert out["estimate"]["status"] == "ok" and out["proposal"]["kind"] == "valuation"


@pytest.mark.parametrize(
    "code,status",
    [("permission_denied", "permission_denied"), ("rate_limit_exceeded", "failed")],
)
def test_refusals_come_back_as_data(tmp_path, code, status):
    reply, _ = _run(tmp_path, lambda m: {"error": "Fixed text.", "error_code": code})
    sub = reply["result"]["submission"]
    assert (sub["status"], sub["code"]) == (status, code)


def test_estimate_alone_sends_no_propose(tmp_path):
    reply, seen = _run(tmp_path, lambda m: {"receipts": []}, function="estimate")
    assert reply["result"]["status"] == "ok"
    assert not [m for m in seen if m["type"] == "propose"]
