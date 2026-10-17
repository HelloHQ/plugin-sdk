# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/release_sdk_check.py (stdlib unittest).

python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_sdk_check as rsc

REPO_ROOT = Path(__file__).resolve().parent.parent


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def fake_repo(root: Path, version: str = "1.2.3") -> None:
    write(root, "sdks/rust/Cargo.toml", f'[package]\nname = "hellohq-plugin-sdk"\nversion = "{version}"\n')
    write(root, "sdks/js/package.json", json.dumps({"name": "@hellohq/plugin-sdk", "version": version}))
    write(root, "sdks/python/pyproject.toml", f'[project]\nname = "hellohq-plugin-sdk"\nversion = "{version}"\n')
    write(root, "sdks/go/go.mod", f"module {rsc.GO_MODULE}\n\ngo 1.24\n")


class ParseTag(unittest.TestCase):
    def test_accepts_each_language(self):
        for lang in ("go", "rust", "js", "python"):
            self.assertEqual(rsc.parse_tag(f"sdks/{lang}/v1.2.3"), (lang, "1.2.3"))

    def test_accepts_registry_spelled_prereleases(self):
        self.assertEqual(rsc.parse_tag("sdks/rust/v1.2.3-rc.1"), ("rust", "1.2.3-rc.1"))
        self.assertEqual(rsc.parse_tag("sdks/python/v1.2.3rc1"), ("python", "1.2.3rc1"))

    def test_rejects_malformed_tags(self):
        for tag in (
            "sdks/ruby/v1.2.3",  # unknown language
            "sdks/rust/1.2.3",  # no v
            "sdks/rust/v1.2",  # not semver
            "sdks/rust/v1.2.3/extra",
            "hello-world-v1.0.0",  # a plugin release tag, not ours
            "cli/v0.1.0",
            "sdks/rust/v1.2.3 ",
            "sdks/rust/v1.2.3-",
            "sdks/rust/v1.2.3;rm",
        ):
            with self.assertRaises(rsc.ReleaseError, msg=tag):
                rsc.parse_tag(tag)


class Check(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fake_repo(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_matching_versions_pass(self):
        for lang in ("rust", "js", "python"):
            self.assertEqual(rsc.check(f"sdks/{lang}/v1.2.3", self.root), (lang, "1.2.3"))

    def test_go_has_no_file_version_so_any_tag_passes(self):
        self.assertEqual(rsc.check("sdks/go/v9.9.9", self.root), ("go", "9.9.9"))

    def test_version_mismatch_is_refused_for_each_registry(self):
        for lang in ("rust", "js", "python"):
            with self.assertRaisesRegex(rsc.ReleaseError, "says 1.2.3", msg=lang):
                rsc.check(f"sdks/{lang}/v1.2.4", self.root)

    def test_prerelease_must_match_exactly(self):
        with self.assertRaises(rsc.ReleaseError):
            rsc.check("sdks/rust/v1.2.3-rc.1", self.root)

    def test_wrong_package_name_is_refused(self):
        write(self.root, "sdks/rust/Cargo.toml", '[package]\nname = "other"\nversion = "1.2.3"\n')
        with self.assertRaisesRegex(rsc.ReleaseError, "named 'other'"):
            rsc.check("sdks/rust/v1.2.3", self.root)

    def test_wrong_go_module_is_refused(self):
        write(self.root, "sdks/go/go.mod", "module example.com/x\n")
        with self.assertRaisesRegex(rsc.ReleaseError, "named 'example.com/x'"):
            rsc.check("sdks/go/v1.0.0", self.root)

    def test_missing_package_file_is_refused(self):
        (self.root / "sdks/js/package.json").unlink()
        with self.assertRaisesRegex(rsc.ReleaseError, "missing"):
            rsc.check("sdks/js/v1.2.3", self.root)

    def test_version_inherited_from_a_workspace_is_refused(self):
        write(self.root, "sdks/rust/Cargo.toml", '[package]\nname = "hellohq-plugin-sdk"\nversion.workspace = true\n')
        with self.assertRaisesRegex(rsc.ReleaseError, "literal"):
            rsc.check("sdks/rust/v1.2.3", self.root)


class Main(unittest.TestCase):
    def test_exit_codes_and_github_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake_repo(root)
            out = root / "gh_output"
            with contextlib.redirect_stdout(io.StringIO()):
                ok = rsc.main(["check", "--tag", "sdks/python/v1.2.3", "--root", d, "--github-output", str(out)])
                bad = rsc.main(["check", "--tag", "sdks/python/v9.9.9", "--root", d])
            self.assertEqual((ok, bad), (0, 1))
            self.assertEqual(out.read_text(), "lang=python\nversion=1.2.3\n")


class RealRepo(unittest.TestCase):
    """The packages this repo ships must be publishable under their own versions."""

    def test_each_sdk_matches_a_tag_of_its_own_version(self):
        for lang in ("rust", "js", "python"):
            _, version = rsc.package_identity(lang, REPO_ROOT)
            self.assertEqual(rsc.check(f"sdks/{lang}/v{version}", REPO_ROOT), (lang, version))
        self.assertEqual(rsc.check("sdks/go/v0.0.1", REPO_ROOT)[0], "go")


if __name__ == "__main__":
    unittest.main()
