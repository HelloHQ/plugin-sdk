"""The manifest and the plugin source keep the promises the README makes."""

from __future__ import annotations

import ast
import json

from conftest import EXAMPLE_DIR

import plugin

MANIFEST = json.loads((EXAMPLE_DIR / "manifest.json").read_text(encoding="utf-8"))
SIBLING = json.loads(
    (EXAMPLE_DIR.parent / "portfolio_summary" / "manifest.json").read_text(
        encoding="utf-8"
    )
)


def test_permission_set_is_exactly_what_the_report_needs() -> None:
    assert [p["id"] for p in MANIFEST["permissions"]] == [
        "read:portfolio_names",
        "read:asset_count",
        "read:currency_rates",
        "read:aggregated_values",
        "write:external_output",
    ]
    for perm in MANIFEST["permissions"]:
        # Every grant explains itself at install review; none is scoped.
        assert set(perm) == {"id", "reason"}, perm
        assert 10 <= len(perm["reason"]) <= 120, perm


def test_every_context_read_the_plugin_uses_is_declared() -> None:
    declared = {p["id"] for p in MANIFEST["permissions"]}
    used = {plugin.NAMES, plugin.COUNTS, plugin.CURRENCIES, plugin.TOTALS}
    assert used | {"write:external_output"} == declared


def test_no_network_no_ai_no_storage() -> None:
    ids = {p["id"] for p in MANIFEST["permissions"]}
    assert not ids & {
        "network:fetch",
        "ai:inference",
        "plugin:storage",
        "read:external_input",
        "read:sheet_structure",
        "read:workspace_info",
    }


def test_needs_verified_tier_but_does_not_self_declare_it() -> None:
    # read:aggregated_values, write:external_output, sidecar and webview are
    # Verified-only (docs/plugin/03, 04). The registry team sets trust_tier at
    # merge; a plugin must not set it itself.
    ids = {p["id"] for p in MANIFEST["permissions"]}
    assert {"read:aggregated_values", "write:external_output"} <= ids
    assert MANIFEST["execution_mode"] == "sidecar" and MANIFEST["ui_type"] == "webview"
    assert "trust_tier" not in MANIFEST
    assert "publisher_signing_key_id" not in MANIFEST


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
