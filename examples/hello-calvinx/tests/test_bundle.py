"""The shipped artifact: one self-contained, deterministic, standard-library-only plugin.py."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import bundle

ROOT = Path(__file__).resolve().parent.parent

#: Every module the shipped plugin.py may import. Deliberately an allowlist, not
#: "anything in the standard library": nothing here can open a file, a socket or
#: a process.
ALLOWED_IMPORTS = {"__future__", "json", "sys", "typing"}


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import left in the bundle"
            found.add((node.module or "").split(".")[0])
    return found


def _calls(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_bundle_imports_only_the_allowlisted_stdlib_modules():
    tree = ast.parse(bundle.build())
    imports = _imports(tree)
    assert imports <= ALLOWED_IMPORTS, imports - ALLOWED_IMPORTS
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}
    assert "hellohq_plugin_sdk" not in imports


def test_bundle_has_no_file_network_process_or_dynamic_code_access():
    tree = ast.parse(bundle.build())
    banned = {"open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "globals", "locals", "input"}
    assert not (_calls(tree) & banned), _calls(tree) & banned


def test_bundle_carries_no_host_call_machinery():
    """The only host data it uses is the pre-fetched read in ``context``; the SDK's
    host-call module (ai_complete / storage_* / http_request / propose) is not bundled."""
    src = bundle.build()
    for host_call in ("_exchange", "_rpc", "def fetch", "def propose", "def storage_get", "def ai_complete"):
        assert host_call not in src, host_call
    # serve() writes only the ready line and RPC replies; nothing else in the file writes to stdout.
    writes = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute) and n.attr == "write"]
    assert len(writes) == 4, "serve.send (2) + emit_event (2) are the only stdout writes"


def test_bundle_has_unique_top_level_names_and_one_future_import():
    src = bundle.build()
    names: list[str] = []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    assert len(names) == len(set(names)), {n for n in names if names.count(n) > 1}
    assert src.count("from __future__ import annotations") == 1
    assert src.startswith('"""Hello Calvin')


def test_two_builds_are_byte_identical(tmp_path):
    hashes = []
    for name in ("a.py", "b.py"):
        out = tmp_path / name
        subprocess.run([sys.executable, str(ROOT / "bundle.py"), str(out)], check=True, capture_output=True)
        data = out.read_bytes()
        assert b"\r\n" not in data
        hashes.append(hashlib.sha256(data).hexdigest())
    assert hashes[0] == hashes[1]
    assert (tmp_path / "a.py").read_text() == bundle.build()


def test_bundle_byte_compiles(tmp_path):
    out = tmp_path / "plugin.py"
    out.write_text(bundle.build(), encoding="utf-8", newline="\n")
    subprocess.run([sys.executable, "-m", "py_compile", str(out)], check=True)
