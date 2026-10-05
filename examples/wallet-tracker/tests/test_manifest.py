"""manifest.json against the REGISTRY's manifest schema (plugin-registry ``schema/manifest.schema.json``).

Locates the registry as ``$HELLOHQ_PLUGIN_REGISTRY_DIR`` or a sibling ``plugin-registry`` checkout; skips when
absent unless ``HELLOHQ_REQUIRE_REGISTRY=1`` (CI), which fails instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wallet_tracker.proposals import ASSET_KIND

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


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
    # The registry team sets trust_tier at merge; the schema must accept the same manifest once they have.
    assert validator.is_valid({**MANIFEST, "trust_tier": "verified"})


def test_manifest_declares_the_propose_permissions_the_code_uses():
    perms = {p["id"]: p for p in MANIFEST["permissions"]}
    assert {"network:fetch", "propose:holdings", "propose:valuations"} <= set(perms)
    for pid in ("propose:holdings", "propose:valuations"):
        assert perms[pid]["scope"] == {"kinds": [ASSET_KIND]}, (
            "scope.kinds is required and must match what the code proposes"
        )
    assert MANIFEST["execution_mode"] == "sidecar" and MANIFEST["ui_type"] == "headless"
    assert all(set(p) <= {"id", "scope", "reason"} for p in MANIFEST["permissions"]), (
        "the registry schema allows only these keys"
    )
