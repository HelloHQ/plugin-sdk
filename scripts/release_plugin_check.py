#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate and stage a tag-triggered release of one example plugin.

Used by .github/workflows/release-plugin.yml; stdlib only so it runs on a bare
runner. Tests: scripts/test_release_plugin_check.py.

  check --tag TAG [--github-output FILE]
      Parse TAG (`<example-dir>-v<semver>`), check examples/<dir> has build.sh
      and manifest.json, that manifest.version equals the tag's version, and
      that every release URL in the manifest (wasm_url, ui_bundle_url,
      sidebar_icon) is exactly
      https://github.com/HelloHQ/plugin-sdk/releases/download/<TAG>/<basename>.
      Prints the result; with --github-output also appends dir=, version=,
      prerelease=, ui_type=, needs_node=, assets= for later steps.

  stage --tag TAG --out DIR --notes FILE [--commit SHA] [--run-url URL]
        [--tool NAME=VERSION ...]
      Re-run `check`, then require each asset (built by build.sh) to exist in
      examples/<dir>/, copy them to DIR, write DIR/SHA256SUMS (sha256sum
      format) and the release notes (asset / sha256 / bytes table, toolchain,
      source commit) to FILE.

Exit status 1 with a `::error::` line on any problem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_SLUG = "HelloHQ/plugin-sdk"
DOWNLOAD_PREFIX = f"https://github.com/{REPO_SLUG}/releases/download/"
SUMS_NAME = "SHA256SUMS"

# <example-dir>-v<MAJOR.MINOR.PATCH>[-<prerelease>]. The directory is lowercase
# letters/digits joined by single '-' or '_' (no dots, no slashes), so the split
# before "-v<digits>." is unambiguous. No "+build" metadata.
_NUM = r"(?:0|[1-9][0-9]*)"
_PRE_ID = r"[0-9A-Za-z-]+"
TAG_RE = re.compile(
    r"^(?P<dir>[a-z0-9]+(?:[-_][a-z0-9]+)*)"
    rf"-v(?P<version>{_NUM}\.{_NUM}\.{_NUM}(?:-(?P<pre>{_PRE_ID}(?:\.{_PRE_ID})*))?)$"
)
MAX_TAG_LEN = 128

# A release asset name: one safe path segment.
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Manifest fields that name a release asset, in upload order.
URL_FIELDS = ("wasm_url", "ui_bundle_url", "sidebar_icon")


class CheckError(Exception):
    pass


@dataclass
class Release:
    tag: str
    dir: str
    version: str
    prerelease: bool
    ui_type: str
    example: Path
    assets: list[str] = field(default_factory=list)
    needs_node: bool = False


def parse_tag(tag: str) -> tuple[str, str, bool]:
    if not isinstance(tag, str) or len(tag) > MAX_TAG_LEN:
        raise CheckError(f"tag is not a string of at most {MAX_TAG_LEN} characters")
    m = TAG_RE.fullmatch(tag)
    if not m:
        raise CheckError(
            f"tag {tag!r} is not <example-dir>-v<MAJOR.MINOR.PATCH>[-prerelease] "
            "(example-dir: lowercase letters/digits joined by '-' or '_')"
        )
    return m.group("dir"), m.group("version"), m.group("pre") is not None


def _asset_from_url(field_name: str, url: object, tag: str) -> str:
    if not isinstance(url, str):
        raise CheckError(f"manifest.{field_name} must be a string")
    basename = url.rsplit("/", 1)[-1]
    if not ASSET_RE.fullmatch(basename) or basename == SUMS_NAME:
        raise CheckError(
            f"manifest.{field_name} {url!r} does not end in a valid asset file name"
        )
    expected = f"{DOWNLOAD_PREFIX}{tag}/{basename}"
    if url != expected:
        raise CheckError(
            f"manifest.{field_name} is {url!r}; for this tag it must be exactly {expected!r}"
        )
    return basename


def check(tag: str, root: Path) -> Release:
    example_dir, version, prerelease = parse_tag(tag)

    examples = root / "examples"
    example = examples / example_dir
    if example.is_symlink() or not example.is_dir():
        raise CheckError(
            f"examples/{example_dir} does not exist (or is not a directory)"
        )
    if example.resolve().parent != examples.resolve():
        raise CheckError(f"examples/{example_dir} resolves outside examples/")
    for name in ("build.sh", "manifest.json"):
        f = example / name
        if f.is_symlink() or not f.is_file():
            raise CheckError(f"examples/{example_dir}/{name} is missing")

    try:
        manifest = json.loads((example / "manifest.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CheckError(
            f"examples/{example_dir}/manifest.json is not valid JSON: {e}"
        ) from e
    if not isinstance(manifest, dict):
        raise CheckError("manifest.json must be a JSON object")

    if manifest.get("version") != version:
        raise CheckError(
            f"manifest.version is {manifest.get('version')!r} but the tag is for version {version!r}"
        )

    ui_type = manifest.get("ui_type", "declarative")
    if not isinstance(ui_type, str):
        raise CheckError("manifest.ui_type must be a string")

    if manifest.get("wasm_url") is None:
        raise CheckError("manifest.wasm_url is missing")

    assets: list[str] = []
    for name in URL_FIELDS:
        value = manifest.get(name)
        if value is None:
            continue
        # A webview plugin may name its sidebar icon by a path inside its UI
        # bundle instead of a URL (plugin-registry manifest schema); that file
        # ships inside ui.zip, not as a release asset.
        if (
            name == "sidebar_icon"
            and ui_type == "webview"
            and isinstance(value, str)
            and "://" not in value
            and not value.startswith("/")
        ):
            continue
        basename = _asset_from_url(name, value, tag)
        if basename in assets:
            raise CheckError(f"manifest.{name} reuses the asset name {basename!r}")
        assets.append(basename)

    return Release(
        tag=tag,
        dir=example_dir,
        version=version,
        prerelease=prerelease,
        ui_type=ui_type,
        example=example,
        assets=assets,
        needs_node=(example / "ui" / "package.json").is_file(),
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stage(
    release: Release,
    out: Path,
    notes: Path,
    commit: str = "",
    run_url: str = "",
    tools: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for name in release.assets:
        src = release.example / name
        if src.is_symlink() or not src.is_file():
            raise CheckError(
                f"asset {name!r} was not produced in examples/{release.dir}/ by build.sh"
            )
        size = src.stat().st_size
        if size == 0:
            raise CheckError(f"asset {name!r} is empty")
        rows.append((name, _sha256(src), size))

    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise CheckError(f"staging directory {out} is not empty")
    for name, _, _ in rows:
        shutil.copyfile(release.example / name, out / name)
    (out / SUMS_NAME).write_text(
        "".join(f"{digest}  {name}\n" for name, digest, _ in rows), encoding="utf-8"
    )

    lines = [
        (
            f"`examples/{release.dir}` version {release.version}, built by GitHub "
            "Actions from the tagged source (`build.sh`)."
        ),
        "",
        "| Asset | SHA-256 | Bytes |",
        "|---|---|---|",
        *(f"| `{name}` | `{digest}` | {size} |" for name, digest, size in rows),
        "",
        f"`{SUMS_NAME}` lists the same hashes in `sha256sum -c` format.",
        "",
    ]
    if commit:
        lines.append(f"- Source commit: `{commit}`")
    for tool, version in tools or []:
        lines.append(f"- {tool}: `{version}`")
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    lines += [
        "",
        (
            "Every asset has a build provenance attestation; verify with "
            f"`gh attestation verify <asset> --repo {REPO_SLUG}`."
        ),
        "",
        (
            "Plugin-registry manifests pin these hashes, so this release's assets "
            "are never replaced; a fix ships as a new version and tag."
        ),
        "",
    ]
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("\n".join(lines), encoding="utf-8")
    return rows


def _write_outputs(path: Path, release: Release) -> None:
    values = {
        "dir": release.dir,
        "version": release.version,
        "prerelease": "true" if release.prerelease else "false",
        "ui_type": release.ui_type,
        "needs_node": "true" if release.needs_node else "false",
        "assets": " ".join(release.assets),
    }
    for k, v in values.items():
        # Every value is regex-validated or a fixed token; none can carry a
        # newline that would inject another output.
        if "\n" in v or "\r" in v:
            raise CheckError(f"output {k} contains a newline")
    with path.open("a", encoding="utf-8") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def _parse_tool(value: str) -> tuple[str, str]:
    name, sep, version = value.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError("expected NAME=VERSION")
    return name, version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate/stage an example plugin release."
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="plugin-sdk checkout (default: this script's repo)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--tag", required=True)
    c.add_argument("--github-output", type=Path)

    s = sub.add_parser("stage")
    s.add_argument("--tag", required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--notes", type=Path, required=True)
    s.add_argument("--commit", default="")
    s.add_argument("--run-url", default="")
    s.add_argument("--tool", type=_parse_tool, action="append", default=[])

    args = ap.parse_args(argv)
    try:
        release = check(args.tag, args.root)
        if args.cmd == "check":
            if args.github_output:
                _write_outputs(args.github_output, release)
            print(
                f"ok: examples/{release.dir} v{release.version}"
                f"{' (prerelease)' if release.prerelease else ''}; "
                f"ui_type={release.ui_type}; assets: {', '.join(release.assets)}"
            )
        else:
            rows = stage(
                release, args.out, args.notes, args.commit, args.run_url, args.tool
            )
            for name, digest, size in rows:
                print(f"{digest}  {name}  ({size} bytes)")
    except CheckError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
