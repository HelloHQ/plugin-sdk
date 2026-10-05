"""manifest.json against the REGISTRY's manifest schema.

The schema is plugin-registry's ``schema/manifest.schema.json``, found as
``$HELLOHQ_PLUGIN_REGISTRY_DIR`` or a sibling ``plugin-registry`` checkout. Skips when absent
unless ``HELLOHQ_REQUIRE_REGISTRY=1`` (CI), which fails instead.
"""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
    validator = jsonschema.Draft202012Validator(
        _schema(), format_checker=jsonschema.FormatChecker()
    )
    errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(MANIFEST)]
    assert not errors, errors
    # The registry team sets trust_tier at merge; the schema must accept the manifest then too.
    assert validator.is_valid({**MANIFEST, "trust_tier": "verified"})


def test_manifest_proposes_only_valuations_for_homes_with_a_declared_ui_type():
    perms = {p["id"]: p for p in MANIFEST["permissions"]}
    assert perms["propose:valuations"]["scope"] == {"kinds": ["home"]}
    assert "propose:holdings" not in perms
    # Set ui_type explicitly: an absent one trips the schema's webview rule with a confusing error.
    assert MANIFEST["ui_type"] in {"declarative", "headless"}
    # The schema allows only id and scope on a permission.
    assert all(set(p) <= {"id", "scope"} for p in MANIFEST["permissions"])
