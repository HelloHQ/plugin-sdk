"""The sidecar adapter, run both from source and from the single-file bundle build.py ships."""

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fakes import CONTACT, NOW
from fixtures import CIK, FILINGS, submissions_json
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
        mod = _load("filings_plugin_under_test", ROOT / "plugin.py")
    else:
        out = tmp_path / "plugin.py"
        out.write_text(bundle.build())
        mod = _load("filings_bundled_plugin_under_test", out)
    store: dict[str, str] = {}
    monkeypatch.setattr(mod.host, "storage_get", lambda k: store.get(k))
    monkeypatch.setattr(mod.host, "storage_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(mod.SidecarHost, "now", lambda self: NOW)
    monkeypatch.setattr(mod.SidecarHost, "sleep", lambda self, s: None)
    mod._store = store
    return mod


def _args(function, **fn_args):
    return {"context": {}, "input": {"function": function, "args": fn_args}}


def test_contact_then_lookup_end_to_end(plugin, monkeypatch):
    assert plugin.dispatch("run", _args("contact_status"))["configured"] is False
    with pytest.raises(PluginError) as ei:  # refused before any network call
        plugin.dispatch("run", _args("lookup", identifiers=[{"type": "cik", "value": "1234567"}]))
    assert ei.value.code == "invalid_input" and "contact_not_configured" in ei.value.message

    assert plugin.dispatch("run", _args("configure_contact", contact=CONTACT))["configured"]
    assert plugin._store["contact"] == CONTACT

    sent = []

    def fake_fetch(url, *, method="GET", headers=None, body=""):
        sent.append((url, method, dict(headers or {})))
        return {"status": 200, "headers": {}, "body": submissions_json(FILINGS)}

    monkeypatch.setattr(plugin.host, "fetch", fake_fetch)
    out = plugin.dispatch(
        "run",
        _args("lookup", identifiers=[{"type": "cik", "value": "1234567"}], include_facts=False),
    )
    assert out["results"][0]["status"] == "ok" and out["results"][0]["cik"] == CIK
    assert json.loads(json.dumps(out)) == out
    url, method, headers = sent[0]
    assert url == "https://data.sec.gov/submissions/CIK0001234567.json" and method == "GET"
    assert headers["User-Agent"].startswith(CONTACT)


def test_invalid_input_and_unknown_function(plugin):
    with pytest.raises(PluginError) as ei:
        plugin.dispatch("run", _args("lookup", identifiers=[]))
    assert ei.value.code == "invalid_input"
    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", _args("bogus"))


def test_bundle_is_self_contained_and_has_no_duplicate_names():
    text = bundle.build()
    assert "from filings_companion" not in text and "from ." not in text
    assert text.count("from __future__ import annotations") == 1
    tree = ast.parse(text)
    seen: dict[str, int] = {}
    for node in tree.body:
        names = []
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for n in names:
            seen[n] = seen.get(n, 0) + 1
    assert [n for n, c in seen.items() if c > 1] == []
