"""manifest.json against plugin-registry's manifest schema and the release rules.

Locates the registry as ``$HELLOHQ_PLUGIN_REGISTRY_DIR`` or a sibling ``plugin-registry`` checkout; skips when
absent unless ``HELLOHQ_REQUIRE_REGISTRY=1`` (CI), which fails instead.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
RELEASE = "https://github.com/HelloHQ/plugin-sdk/releases/download/hello-calvinx-v1.0.0/"


def _schema():
    env = os.environ.get("HELLOHQ_PLUGIN_REGISTRY_DIR")
    for root in ([Path(env)] if env else []) + [
        ROOT.parents[2] / "plugin-registry",
        ROOT.parents[3] / "plugin-registry",
    ]:
        path = root / "schema" / "manifest.schema.json"
        if path.is_file():
            return json.loads(path.read_text())
    if os.environ.get("HELLOHQ_REQUIRE_REGISTRY") == "1":
        pytest.fail("plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)")
    pytest.skip("plugin-registry checkout not found (set HELLOHQ_PLUGIN_REGISTRY_DIR)")


def test_manifest_validates_against_the_registry_schema():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(_schema(), format_checker=jsonschema.FormatChecker())
    errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(MANIFEST)]
    assert not errors, errors


def test_manifest_surface_is_one_read_permission():
    assert MANIFEST["permissions"] == [{"id": "read:portfolio_names"}]
    assert MANIFEST["execution_mode"] == "sidecar"
    assert MANIFEST["ui_type"] == "declarative"
    # A third-party plugin: no tier in the repo copy (the registry team sets it at merge) and no
    # HelloHQ provenance.
    assert "trust_tier" not in MANIFEST
    assert MANIFEST["provenance"] == "community"
    assert MANIFEST["id"].startswith("com.calvinx.")
    assert MANIFEST["licensing"] == {"kind": "open_source", "spdx": "Apache-2.0"}
    # Stdlib only: no python_runtime (no dependencies to install; the registry schema has no such field).
    assert "python_runtime" not in MANIFEST


def test_manifest_meets_the_registry_ci_rules_for_a_sidecar():
    # plugin-registry .github/workflows/validate.yml step 6b: sidecar is a v2 capability.
    assert int(MANIFEST["min_host_version"].split(".")[0]) >= 2
    # Step 6: sidecar is Verified-only. The registry team sets trust_tier: verified in the registry PR
    # (an author-signed plugin signs the statement of the Verified manifest), so the repo copy has none.
    assert MANIFEST["execution_mode"] == "sidecar"
    assert "trust_tier" not in MANIFEST


def test_release_urls_name_the_built_and_committed_assets():
    assert MANIFEST["version"] == "1.0.0"
    assert MANIFEST["wasm_url"] == RELEASE + "plugin.py"
    assert MANIFEST["sidebar_icon"] == RELEASE + "icon.svg"
    # The repo copy keeps the placeholder; `hqplugin publish` pins the served hash.
    assert MANIFEST["content_hash_sha256"] == "0" * 64
    assert (ROOT / "icon.svg").is_file()


def test_icon_is_a_plain_single_fill_svg():
    """The registry's rules (plugin-registry scripts/verify-artifacts.mjs ``svgIconProblem``)."""
    raw = (ROOT / "icon.svg").read_bytes()
    assert len(raw) <= 64 * 1024
    text = raw.decode("utf-8")
    assert re.search(r"<svg[\s>/]", text, re.I)
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
        assert not re.search(pattern, text, re.I), pattern
    assert 'viewBox="0 0 24 24"' in text and text.count("<path") == 1 and 'fill="currentColor"' in text
