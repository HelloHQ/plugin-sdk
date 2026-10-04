import ast
import importlib.util
import re
import sys
from pathlib import Path

import bundle

ROOT = Path(__file__).resolve().parent.parent


def test_bundle_has_unique_top_level_names_and_no_internal_imports():
    src = bundle.build(ROOT)
    tree = ast.parse(src)  # embedded modules are string literals, so only inlined names are visible here
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, dupes
    assert not re.search(r"^\s*(from|import) macro_context", src, re.M)  # no leftover imports
    assert len(re.findall(r"^from __future__ import annotations", src, re.M)) == 1


def test_bundled_file_runs_standalone_and_matches_the_package(tmp_path, monkeypatch):
    out = tmp_path / "plugin.py"
    out.write_text(bundle.build(ROOT))
    # Import it with the package path hidden, so it cannot lean on macro_context.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != ROOT])
    for mod in [m for m in sys.modules if m.startswith("macro_context")]:
        monkeypatch.delitem(sys.modules, mod)
    spec = importlib.util.spec_from_file_location("bundled_plugin", out)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "bundled_plugin", module)  # dataclasses needs it registered
    spec.loader.exec_module(module)
    assert callable(module.dispatch) and module.parse_request({}).sections == ("ecb", "worldbank", "treasury", "sgfx")
    assert module.ecb.parse_response is not module.worldbank.parse_response  # embedded modules stay separate
    assert module.sgfx.RESOURCE_ID.startswith("d_")
    assert sys.modules["macro_context.ecb"] is module.ecb  # the bundle registered its own embedded copies
