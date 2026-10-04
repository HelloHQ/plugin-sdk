"""The manifest and the plugin source keep the promises the README makes."""

from __future__ import annotations

import ast
import json

from conftest import EXAMPLE_DIR

MANIFEST = json.loads((EXAMPLE_DIR / "manifest.json").read_text(encoding="utf-8"))
SIBLING = json.loads(
    (EXAMPLE_DIR.parent / "portfolio_summary" / "manifest.json").read_text(
        encoding="utf-8"
    )
)


def test_permission_set_is_exactly_what_the_report_needs() -> None:
    assert MANIFEST["permissions"] == [
        {"id": "read:portfolio_names"},
        {"id": "read:aggregated_values"},
        {"id": "write:external_output"},
    ]


def test_no_network_no_ai_no_storage() -> None:
    ids = {p["id"] for p in MANIFEST["permissions"]}
    assert not ids & {
        "network:fetch",
        "ai:inference",
        "plugin:storage",
        "read:external_input",
    }
    assert "scope" not in json.dumps(
        MANIFEST["permissions"]
    )  # same as the sibling examples


def test_manifest_has_the_same_shape_as_the_existing_sidecar_examples() -> None:
    assert set(MANIFEST) == set(SIBLING)
    for key in (
        "execution_mode",
        "ui_type",
        "entry_function",
        "license",
        "author",
        "repo",
    ):
        assert MANIFEST[key] == SIBLING[key], key
    assert MANIFEST["execution_mode"] == "sidecar" and MANIFEST["ui_type"] == "webview"
    assert set(MANIFEST["categories"]) <= {
        "analytics", "import", "export", "productivity", "finance",
        "reporting", "utilities", "charts", "integration",
    }  # fmt: skip
    assert len(MANIFEST["categories"]) <= 3
    assert len(MANIFEST["description"]) <= 200
    # Committed hashes are placeholders until release (like every example).
    assert set(MANIFEST["content_hash_sha256"]) == {"0"}
    assert set(MANIFEST["ui_bundle_hash_sha256"]) == {"0"}
    assert "report-pack" in MANIFEST["wasm_url"] and MANIFEST["wasm_url"].endswith(
        "plugin.py"
    )


def test_plugin_source_imports_nothing_that_can_reach_the_network_or_ai() -> None:
    tree = ast.parse((EXAMPLE_DIR / "plugin.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "__future__", "html", "math", "re", "unicodedata", "datetime", "decimal",
        "typing", "hellohq_plugin_sdk",
    }, imported  # fmt: skip
    source = (EXAMPLE_DIR / "plugin.py").read_text(encoding="utf-8")
    for call in (
        "ai_complete",
        "http_request",
        "storage_get",
        "storage_set",
        "emit_event",
    ):
        assert call not in source
