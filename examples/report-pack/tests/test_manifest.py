"""The manifest and the plugin source keep the promises the README makes."""

from __future__ import annotations

import ast
import json

from conftest import EXAMPLE_DIR

import report_pack as plugin

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


def test_is_a_first_party_official_plugin() -> None:
    # read:aggregated_values, write:external_output, sidecar and webview are
    # Verified-only (docs/plugin/03, 04); an Official plugin satisfies that. The
    # id sits in HelloHQ's reserved namespace, which no author certificate can
    # cover, and the Official signer signs it (hqplugin publish --first-party).
    ids = {p["id"] for p in MANIFEST["permissions"]}
    assert {"read:aggregated_values", "write:external_output"} <= ids
    assert MANIFEST["execution_mode"] == "sidecar" and MANIFEST["ui_type"] == "webview"
    assert MANIFEST["id"] == "com.hellohq.report-pack"
    assert (MANIFEST["trust_tier"], MANIFEST["provenance"]) == ("official", "core")
    assert MANIFEST["licensing"] == {"kind": "open_source", "spdx": "Apache-2.0"}
    assert "publisher_signing_key_id" not in MANIFEST


def test_manifest_has_the_same_shape_as_the_existing_sidecar_examples() -> None:
    # The sibling is a plain example; this one is also Official and has an icon.
    assert set(MANIFEST) == set(SIBLING) | {
        "trust_tier", "provenance", "licensing", "sidebar_icon",
    }  # fmt: skip
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
    assert MANIFEST["sidebar_icon"].endswith("/report-pack-v1.0.0/icon.svg")


def test_plugin_source_imports_nothing_that_can_reach_the_network_or_ai() -> None:
    tree = ast.parse((EXAMPLE_DIR / "report_pack.py").read_text(encoding="utf-8"))
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
    source = (EXAMPLE_DIR / "report_pack.py").read_text(encoding="utf-8")
    for call in (
        "ai_complete",
        "http_request",
        "storage_get",
        "storage_set",
        "emit_event",
    ):
        assert call not in source


def test_icon_is_a_plain_single_fill_svg() -> None:
    """The registry's rules (plugin-registry scripts/verify-artifacts.mjs ``svgIconProblem``)."""
    import re

    raw = (EXAMPLE_DIR / "icon.svg").read_bytes()
    assert len(raw) <= 64 * 1024
    text = raw.decode("utf-8")
    assert re.search(r"<svg[\s>/]", text, re.IGNORECASE)
    banned = [
        r"<script",
        r"<foreignObject",
        r"<!ENTITY",
        r"<!DOCTYPE",
        r"\son[a-z]+\s*=",
        r"@import",
        r"javascript:",
        r"(?:xlink:)?href\s*=\s*[\"'](?!#)",
        r"url\(\s*[\"']?(?!#)",
        r"<(?:image|use|iframe|embed|object|audio|video|link|style)\b",
    ]
    for pattern in banned:
        assert not re.search(pattern, text, re.IGNORECASE), pattern
    assert 'viewBox="0 0 24 24"' in text
    assert text.count("<path") == 1 and 'fill="currentColor"' in text
