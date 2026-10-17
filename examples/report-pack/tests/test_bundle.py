"""``bundle.py`` turns the SDK's sidecar loop and ``report_pack.py`` into the one
file the host runs. The app installs nothing, so the file must import only the
standard library, and it must be byte-for-byte reproducible."""

from __future__ import annotations

import ast
import json
import subprocess
import sys

from conftest import EXAMPLE_DIR

import bundle

STDLIB_ONLY = set(sys.stdlib_module_names)


def _imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import survived bundling"
            found.add((node.module or "").split(".")[0])
    return found


def test_bundle_imports_only_the_standard_library() -> None:
    imports = _imports(bundle.build())
    assert imports <= STDLIB_ONLY, sorted(imports - STDLIB_ONLY)
    assert "hellohq_plugin_sdk" not in imports


def test_bundle_is_reproducible() -> None:
    assert bundle.build() == bundle.build()
    assert "\r" not in bundle.build()


def test_bundle_keeps_the_line_separators_the_source_holds_in_strings() -> None:
    # report_pack.py names U+2028/U+2029 (and other characters str.splitlines()
    # splits on) inside string literals. Joining a splitlines() result with "\n"
    # once broke the file; the bundle must still compile and keep them.
    source = (EXAMPLE_DIR / "report_pack.py").read_text(encoding="utf-8")
    assert " " in source and " " in source
    built = bundle.build()
    compile(built, "plugin.py", "exec")
    assert " " in built and " " in built


def test_bundle_carries_the_sdk_loop_and_the_plugin_once() -> None:
    built = bundle.build()
    top_level = [
        node.name for node in ast.parse(built).body if isinstance(node, ast.FunctionDef)
    ]
    assert top_level.count("serve") == 1
    assert top_level.count("dispatch") == 1
    assert built.count("from __future__ import annotations") == 1
    assert built.rstrip().endswith("serve(dispatch)")


def test_bundle_runs_in_isolated_mode_with_no_sdk(tmp_path) -> None:
    out = tmp_path / "plugin.py"
    out.write_text(bundle.build(), encoding="utf-8", newline="\n")
    request = {
        "id": 1,
        "function": "run",
        "args": {"context": {}, "input": {"function": "report", "args": {}}},
    }
    done = subprocess.run(
        [sys.executable, "-I", str(out)],
        input=json.dumps(request) + '\n{"type":"shutdown"}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        env={},
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    ready, reply = (json.loads(line) for line in done.stdout.splitlines())
    assert ready["type"] == "ready" and reply["id"] == 1
    assert reply["result"]["data_status"] == "no_portfolios"
