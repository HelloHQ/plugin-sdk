# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/release_plugin_check.py (stdlib unittest).

python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_plugin_check as rpc

REPO_ROOT = Path(__file__).resolve().parent.parent
DL = "https://github.com/HelloHQ/plugin-sdk/releases/download"


def manifest(tag: str, version: str, **extra) -> dict:
    m = {
        "id": "com.example.demo",
        "version": version,
        "execution_mode": "wasm",
        "ui_type": "declarative",
        "wasm_url": f"{DL}/{tag}/plugin.wasm",
        "sidebar_icon": f"{DL}/{tag}/icon.svg",
    }
    m.update(extra)
    return {k: v for k, v in m.items() if v is not None}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "examples").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def example(self, name: str, man: dict | None, build_sh: bool = True) -> Path:
        d = self.root / "examples" / name
        d.mkdir(parents=True)
        if build_sh:
            (d / "build.sh").write_text("#!/usr/bin/env bash\n")
        if man is not None:
            (d / "manifest.json").write_text(json.dumps(man))
        return d

    def assertRejects(self, tag: str, fragment: str) -> None:
        with self.assertRaises(rpc.CheckError) as cm:
            rpc.check(tag, self.root)
        self.assertIn(fragment, str(cm.exception))


class TagFormat(unittest.TestCase):
    def test_good_tags(self) -> None:
        cases = {
            "hello-world-v1.0.0": ("hello-world", "1.0.0", False),
            "portfolio_overview-v10.20.30": ("portfolio_overview", "10.20.30", False),
            "fx-advisor-v0.1.0-rc.1": ("fx-advisor", "0.1.0-rc.1", True),
            "a-v0.0.0": ("a", "0.0.0", False),
            # The directory is everything before the LAST "-v<semver>".
            "x-v1-v2.0.0": ("x-v1", "2.0.0", False),
        }
        for tag, want in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(rpc.parse_tag(tag), want)

    def test_bad_tags(self) -> None:
        bad = [
            "",
            "v1.0.0",
            "hello-world",
            "hello-world-1.0.0",
            "hello-world-v1.0",
            "hello-world-v1",
            "hello-world-v01.0.0",
            "hello-world-v1.0.0+build.5",
            "hello-world-v1.0.0-",
            "hello-world-v1.0.0-rc..1",
            "Hello-World-v1.0.0",
            "hello--world-v1.0.0",
            "-hello-v1.0.0",
            "hello-world-V1.0.0",
            "../hello-world-v1.0.0",
            "examples/hello-world-v1.0.0",
            "hello.world-v1.0.0",
            "hello world-v1.0.0",
            "hello-world-v1.0.0\n",
            "hello-world-v1.0.0;rm -rf",
            "a" * 130 + "-v1.0.0",
        ]
        for tag in bad:
            with self.subTest(tag=tag), self.assertRaises(rpc.CheckError):
                rpc.parse_tag(tag)


class Check(Base):
    def test_good_release(self) -> None:
        tag = "demo-v1.2.3"
        self.example("demo", manifest(tag, "1.2.3"))
        r = rpc.check(tag, self.root)
        self.assertEqual((r.dir, r.version, r.prerelease), ("demo", "1.2.3", False))
        self.assertEqual(r.assets, ["plugin.wasm", "icon.svg"])
        self.assertFalse(r.needs_node)

    def test_prerelease(self) -> None:
        tag = "demo-v2.0.0-beta.1"
        self.example("demo", manifest(tag, "2.0.0-beta.1"))
        self.assertTrue(rpc.check(tag, self.root).prerelease)

    def test_webview_with_bundle_and_node(self) -> None:
        tag = "lens-v1.0.0"
        d = self.example(
            "lens",
            manifest(
                tag,
                "1.0.0",
                ui_type="webview",
                wasm_url=f"{DL}/{tag}/lens.component.wasm",
                ui_bundle_url=f"{DL}/{tag}/ui.zip",
                sidebar_icon=None,
            ),
        )
        (d / "ui").mkdir()
        (d / "ui" / "package.json").write_text("{}")
        r = rpc.check(tag, self.root)
        self.assertEqual(r.assets, ["lens.component.wasm", "ui.zip"])
        self.assertTrue(r.needs_node)
        self.assertEqual(r.ui_type, "webview")

    def test_webview_icon_may_be_a_bundle_path(self) -> None:
        tag = "lens-v1.0.0"
        self.example(
            "lens",
            manifest(
                tag,
                "1.0.0",
                ui_type="webview",
                ui_bundle_url=f"{DL}/{tag}/ui.zip",
                sidebar_icon="icons/lens.svg",
            ),
        )
        self.assertEqual(rpc.check(tag, self.root).assets, ["plugin.wasm", "ui.zip"])

    def test_declarative_icon_must_be_a_release_url(self) -> None:
        tag = "demo-v1.0.0"
        self.example("demo", manifest(tag, "1.0.0", sidebar_icon="icon.svg"))
        self.assertRejects(tag, "manifest.sidebar_icon")

    def test_missing_example_dir(self) -> None:
        self.assertRejects("nope-v1.0.0", "examples/nope does not exist")

    def test_missing_build_sh(self) -> None:
        self.example("demo", manifest("demo-v1.0.0", "1.0.0"), build_sh=False)
        self.assertRejects("demo-v1.0.0", "build.sh is missing")

    def test_missing_manifest(self) -> None:
        self.example("demo", None)
        self.assertRejects("demo-v1.0.0", "manifest.json is missing")

    def test_invalid_manifest_json(self) -> None:
        d = self.example("demo", None)
        (d / "manifest.json").write_text("{not json")
        self.assertRejects("demo-v1.0.0", "not valid JSON")

    def test_version_mismatch(self) -> None:
        self.example("demo", manifest("demo-v1.0.1", "1.0.0"))
        self.assertRejects("demo-v1.0.1", "manifest.version is '1.0.0'")

    def test_missing_wasm_url(self) -> None:
        self.example("demo", manifest("demo-v1.0.0", "1.0.0", wasm_url=None))
        self.assertRejects("demo-v1.0.0", "wasm_url is missing")

    def test_url_points_at_another_tag(self) -> None:
        tag = "demo-v1.0.1"
        self.example(
            "demo", manifest(tag, "1.0.1", wasm_url=f"{DL}/demo-v1.0.0/plugin.wasm")
        )
        self.assertRejects(tag, "manifest.wasm_url")

    def test_url_points_at_another_repo(self) -> None:
        tag = "demo-v1.0.0"
        self.example(
            "demo",
            manifest(
                tag,
                "1.0.0",
                wasm_url=f"https://github.com/evil/plugin-sdk/releases/download/{tag}/plugin.wasm",
            ),
        )
        self.assertRejects(tag, "must be exactly")

    def test_url_variants_rejected(self) -> None:
        tag = "demo-v1.0.0"
        variants = [
            f"http://github.com/HelloHQ/plugin-sdk/releases/download/{tag}/plugin.wasm",
            f"https://github.com/hellohq/plugin-sdk/releases/download/{tag}/plugin.wasm",
            f"{DL}/{tag}/plugin.wasm?x=1",
            f"{DL}/{tag}/sub/plugin.wasm",
            f"{DL}/{tag}/",
            f"{DL}/{tag}/SHA256SUMS",
            f"{DL}/{tag}/.hidden",
        ]
        for i, url in enumerate(variants):
            with self.subTest(url=url):
                name = f"demo{i}"
                t = f"{name}-v1.0.0"
                self.example(name, manifest(t, "1.0.0", wasm_url=url.replace(tag, t)))
                with self.assertRaises(rpc.CheckError):
                    rpc.check(t, self.root)

    def test_ui_bundle_url_checked(self) -> None:
        tag = "demo-v1.0.0"
        self.example(
            "demo", manifest(tag, "1.0.0", ui_bundle_url=f"{DL}/demo-v0.9.0/ui.zip")
        )
        self.assertRejects(tag, "manifest.ui_bundle_url")

    def test_duplicate_asset_names(self) -> None:
        tag = "demo-v1.0.0"
        self.example(
            "demo", manifest(tag, "1.0.0", sidebar_icon=f"{DL}/{tag}/plugin.wasm")
        )
        self.assertRejects(tag, "reuses the asset name")

    def test_non_string_url(self) -> None:
        self.example("demo", manifest("demo-v1.0.0", "1.0.0", wasm_url=42))
        self.assertRejects("demo-v1.0.0", "must be a string")

    def test_symlinked_example_rejected(self) -> None:
        real = self.root / "elsewhere"
        real.mkdir()
        (real / "build.sh").write_text("")
        (real / "manifest.json").write_text(
            json.dumps(manifest("demo-v1.0.0", "1.0.0"))
        )
        (self.root / "examples" / "demo").symlink_to(real)
        self.assertRejects("demo-v1.0.0", "does not exist")


class Stage(Base):
    def setUp(self) -> None:
        super().setUp()
        self.tag = "demo-v1.0.0"
        self.dir = self.example("demo", manifest(self.tag, "1.0.0"))
        self.out = self.root / "_release"
        self.notes = self.root / "notes.md"

    def test_stages_assets_sums_and_notes(self) -> None:
        (self.dir / "plugin.wasm").write_bytes(b"\0asm-component")
        (self.dir / "icon.svg").write_text("<svg/>")
        rows = rpc.stage(
            rpc.check(self.tag, self.root),
            self.out,
            self.notes,
            commit="abc123",
            run_url="https://example.invalid/run/1",
            tools=[("rustc", "rustc 1.97.1"), ("wasm-tools", "wasm-tools 1.252.0")],
        )
        want = {
            "plugin.wasm": hashlib.sha256(b"\0asm-component").hexdigest(),
            "icon.svg": hashlib.sha256(b"<svg/>").hexdigest(),
        }
        self.assertEqual({n: d for n, d, _ in rows}, want)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()),
            ["SHA256SUMS", "icon.svg", "plugin.wasm"],
        )
        self.assertEqual(
            (self.out / "SHA256SUMS").read_text(),
            f"{want['plugin.wasm']}  plugin.wasm\n{want['icon.svg']}  icon.svg\n",
        )
        notes = self.notes.read_text()
        self.assertIn(f"| `plugin.wasm` | `{want['plugin.wasm']}` | 14 |", notes)
        self.assertIn(f"| `icon.svg` | `{want['icon.svg']}` | 6 |", notes)
        self.assertIn("Source commit: `abc123`", notes)
        self.assertIn("rustc: `rustc 1.97.1`", notes)
        self.assertIn("wasm-tools: `wasm-tools 1.252.0`", notes)

    def test_missing_asset(self) -> None:
        (self.dir / "plugin.wasm").write_bytes(b"x")
        with self.assertRaises(rpc.CheckError) as cm:
            rpc.stage(rpc.check(self.tag, self.root), self.out, self.notes)
        self.assertIn("'icon.svg' was not produced", str(cm.exception))

    def test_empty_asset(self) -> None:
        (self.dir / "plugin.wasm").write_bytes(b"")
        (self.dir / "icon.svg").write_text("<svg/>")
        with self.assertRaises(rpc.CheckError) as cm:
            rpc.stage(rpc.check(self.tag, self.root), self.out, self.notes)
        self.assertIn("is empty", str(cm.exception))

    def test_non_empty_staging_dir(self) -> None:
        (self.dir / "plugin.wasm").write_bytes(b"x")
        (self.dir / "icon.svg").write_text("<svg/>")
        self.out.mkdir()
        (self.out / "stale").write_text("")
        with self.assertRaises(rpc.CheckError):
            rpc.stage(rpc.check(self.tag, self.root), self.out, self.notes)


class Cli(Base):
    def test_check_writes_github_outputs(self) -> None:
        tag = "demo-v1.0.0-rc.2"
        self.example("demo", manifest(tag, "1.0.0-rc.2"))
        out = self.root / "gh_output"
        code = rpc.main(
            [
                "--root",
                str(self.root),
                "check",
                "--tag",
                tag,
                "--github-output",
                str(out),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            out.read_text().splitlines(),
            [
                "dir=demo",
                "version=1.0.0-rc.2",
                "prerelease=true",
                "ui_type=declarative",
                "needs_node=false",
                "assets=plugin.wasm icon.svg",
            ],
        )

    def test_check_failure_exit_code(self) -> None:
        self.assertEqual(
            rpc.main(["--root", str(self.root), "check", "--tag", "bogus"]), 1
        )


class RealRepo(unittest.TestCase):
    """The examples this repo actually releases pass the check."""

    def test_hello_world_v1_0_0(self) -> None:
        r = rpc.check("hello-world-v1.0.0", REPO_ROOT)
        self.assertEqual(r.dir, "hello-world")
        self.assertEqual(r.assets, ["plugin.wasm", "icon.svg"])
        self.assertTrue((REPO_ROOT / "examples" / "hello-world" / "icon.svg").is_file())

    def test_hello_world_wrong_version(self) -> None:
        with self.assertRaises(rpc.CheckError):
            rpc.check("hello-world-v1.0.1", REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
