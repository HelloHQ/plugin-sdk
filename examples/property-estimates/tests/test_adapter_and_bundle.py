"""The sidecar adapter, run both from source and from the single-file bundle build.py ships."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fakes import NOW
from fixtures import sg_page, sg_record
from hellohq_plugin_sdk import PluginError, UnsupportedFunction
from hellohq_plugin_sdk.proposals import (
    Outcome,
    ProposeBadRequest,
    ProposeHostError,
    ProposePermissionDenied,
    ProposeQuotaExceeded,
    ProposeRateLimited,
    ProposeUnsupported,
    Receipt,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bundle  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=["source", "bundle"])
def plugin(request, tmp_path, monkeypatch):
    if request.param == "source":
        mod = _load("plugin_under_test", ROOT / "plugin.py")
    else:
        out = tmp_path / "plugin.py"
        out.write_text(bundle.build())
        mod = _load("bundled_plugin_under_test", out)
    monkeypatch.setattr(mod.SidecarHost, "now", lambda self: NOW)
    monkeypatch.setattr(mod.SidecarHost, "sleep", lambda self, s: None)
    return mod


def _args(function, **fn_args):
    return {"context": {}, "input": {"function": function, "args": fn_args}}


def test_estimate_maps_fetch_and_returns_json(plugin, monkeypatch):
    calls = []
    page = sg_page([sg_record("2026-08", 500_000 + i * 1000, area="90") for i in range(40)])

    def fake_fetch(url, *, method="GET", headers=None, body=""):
        calls.append((url, method, dict(headers or {}), body))
        return {"status": 200, "headers": {}, "body": page}

    monkeypatch.setattr(plugin.host, "fetch", fake_fetch)
    out = plugin.dispatch(
        "run", _args("estimate", region="sg_hdb", town="BISHAN", flat_type="4 ROOM")
    )
    assert out["status"] == "ok" and out["label"].startswith("Information only")
    assert json.loads(json.dumps(out)) == out
    assert calls[0][0].startswith("https://data.gov.sg/api/action/datastore_search?")
    assert calls[0][1] == "GET" and calls[0][3] == ""


def test_invalid_input_maps_to_plugin_error(plugin):
    with pytest.raises(PluginError) as ei:
        plugin.dispatch("run", _args("estimate", region="nowhere"))
    assert ei.value.code == "invalid_input" and "[invalid_input]" in ei.value.message


def _propose_args():
    return _args("propose", region="sg_hdb", town="BISHAN", flat_type="4 ROOM")


def _with_sales(plugin, monkeypatch):
    page = sg_page([sg_record("2026-08", 500_000 + i * 1000, area="90") for i in range(40)])
    monkeypatch.setattr(
        plugin.host, "fetch", lambda url, **kw: {"status": 200, "headers": {}, "body": page}
    )


def test_propose_submits_through_the_sdk_and_reports_receipts(plugin, monkeypatch):
    _with_sales(plugin, monkeypatch)
    sent = []

    def fake_propose(batch):
        sent.append(batch)
        return [Receipt(0, Outcome.QUEUED)]

    monkeypatch.setattr(plugin.host, "propose", fake_propose)
    out = plugin.dispatch("run", _propose_args())
    assert json.loads(json.dumps(out)) == out
    assert out["submission"] == {
        "status": "submitted",
        "receipts": [{"index": 0, "outcome": "queued"}],
    }
    (proposal,) = sent[0]
    assert out["proposal"] == proposal and out["estimate"]["status"] == "ok"
    assert set(proposal) == {"kind", "source_key", "value", "as_of", "method", "source"}
    assert proposal["as_of"] == "2026-10-04T00:00:00Z"


@pytest.mark.parametrize("outcome", ["duplicate", "superseded_older", "unchanged", "suppressed"])
def test_propose_reports_every_other_outcome(plugin, monkeypatch, outcome):
    _with_sales(plugin, monkeypatch)
    monkeypatch.setattr(plugin.host, "propose", lambda batch: [Receipt(0, Outcome(outcome))])
    out = plugin.dispatch("run", _propose_args())
    assert out["submission"]["receipts"] == [{"index": 0, "outcome": outcome}]


def test_propose_reports_an_invalid_receipt_with_its_reason(plugin, monkeypatch):
    _with_sales(plugin, monkeypatch)
    monkeypatch.setattr(
        plugin.host,
        "propose",
        lambda batch: [Receipt(0, Outcome.INVALID, "fetched_at_outside_run")],
    )
    out = plugin.dispatch("run", _propose_args())
    assert out["submission"]["receipts"] == [
        {"index": 0, "outcome": "invalid", "reason": "fetched_at_outside_run"}
    ]


def test_unknown_method_is_the_pending_host_support_fallback(plugin, monkeypatch):
    _with_sales(plugin, monkeypatch)

    def refuse(batch):
        raise ProposeUnsupported("unknown_method:propose", "unknown_method")

    monkeypatch.setattr(plugin.host, "propose", refuse)
    out = plugin.dispatch("run", _propose_args())
    assert out["submission"]["status"] == "pending_host_support"
    assert out["submission"]["receipts"] == []
    assert out["estimate"]["status"] == "ok" and out["proposal"]["kind"] == "valuation"


def test_an_sdk_without_propose_is_the_same_fallback(plugin, monkeypatch):
    _with_sales(plugin, monkeypatch)
    monkeypatch.setitem(sys.modules, "hellohq_plugin_sdk.proposals", None)  # SDK < 0.2.0
    out = plugin.dispatch("run", _propose_args())
    assert out["submission"]["status"] == "pending_host_support"
    assert ">= 0.2.0" in out["submission"]["message"]


@pytest.mark.parametrize(
    "error,status,retryable",
    [
        (ProposePermissionDenied("no grant", "permission_denied"), "permission_denied", False),
        (ProposeRateLimited("slow", "rate_limit_exceeded"), "failed", True),
        (ProposeQuotaExceeded("full", "quota_exceeded"), "failed", True),
        (ProposeBadRequest("bad", "bad_request", "bad_schema"), "failed", False),
        (ProposeHostError("boom", "host_error"), "failed", False),
    ],
)
def test_refusals_are_reported_as_data(plugin, monkeypatch, error, status, retryable):
    _with_sales(plugin, monkeypatch)

    def refuse(batch):
        raise error

    monkeypatch.setattr(plugin.host, "propose", refuse)
    out = plugin.dispatch("run", _propose_args())
    sub = out["submission"]
    assert (sub["status"], sub["code"], sub["retryable"]) == (status, error.code, retryable)
    assert sub["receipts"] == [] and out["estimate"]["status"] == "ok"


def test_http_failure_is_a_clean_error(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin.host, "fetch", lambda url, **kw: {"status": 429, "headers": {}, "body": ""}
    )
    with pytest.raises(PluginError) as ei:
        plugin.dispatch(
            "run", _args("estimate", region="sg_hdb", town="BISHAN", flat_type="4 ROOM")
        )
    assert "rate_limited" in ei.value.message


def test_unknown_function(plugin):
    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", _args("bogus"))


def test_bundle_is_self_contained_and_compiles(tmp_path):
    text = bundle.build()
    assert "from property_estimates" not in text and "from ." not in text
    assert text.count("from __future__ import annotations") == 1
    compile(text, "plugin.py", "exec")


def test_bundle_has_no_duplicate_top_level_definitions():
    import ast

    tree = ast.parse(bundle.build())
    seen: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            seen[node.name] = seen.get(node.name, 0) + 1
    assert [n for n, c in seen.items() if c > 1] == []


def test_binary_body_is_refused_not_stringified(plugin, monkeypatch):
    # The SDK returns bytes when the host sent the body base64 (not UTF-8).
    # str(bytes) would hand "b'...'" to the parsers; the adapter refuses instead.
    monkeypatch.setattr(
        plugin.host,
        "fetch",
        lambda url, **kw: {
            "status": 200,
            "headers": {},
            "body": b"\xff\xfe",
            "body_bytes": b"\xff\xfe",
            "body_encoding": "base64",
        },
    )
    with pytest.raises(Exception) as ei:
        plugin.SidecarHost().fetch(plugin.FetchRequest(url="https://example.invalid/x"))
    assert getattr(ei.value, "code", None) == "unexpected_response_shape"
