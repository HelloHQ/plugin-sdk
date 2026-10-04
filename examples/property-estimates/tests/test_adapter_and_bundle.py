"""The sidecar adapter, run both from source and from the single-file bundle build.py ships."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fakes import NOW
from fixtures import sg_page, sg_record
from hellohq_plugin_sdk import PluginError, UnsupportedFunction

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


def test_propose_is_pending_host_support_and_sends_nothing(plugin, monkeypatch):
    page = sg_page([sg_record("2026-08", 500_000 + i * 1000, area="90") for i in range(40)])
    monkeypatch.setattr(
        plugin.host, "fetch", lambda url, **kw: {"status": 200, "headers": {}, "body": page}
    )
    with pytest.raises(PluginError) as ei:
        plugin.dispatch("run", _args("propose", region="sg_hdb", town="BISHAN", flat_type="4 ROOM"))
    assert "pending_host_support" in ei.value.message


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
