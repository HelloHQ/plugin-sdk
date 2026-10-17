#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate a tag-triggered SDK release before anything is published.

Used by .github/workflows/release-sdks.yml; stdlib only (Python >= 3.11, for
tomllib) so it runs on a bare runner. Tests: scripts/test_release_sdk_check.py.

  check --tag TAG [--root DIR] [--github-output FILE]
      Parse TAG (`sdks/<lang>/v<version>`, lang one of go, rust, js, python),
      then require the version in that SDK's own package file to equal the tag's
      version exactly, and the package to carry the name we publish it under:

        rust    sdks/rust/Cargo.toml       [package]  hellohq-plugin-sdk
        js      sdks/js/package.json                  @hellohq/plugin-sdk
        python  sdks/python/pyproject.toml [project]  hellohq-plugin-sdk
        go      sdks/go/go.mod             module     github.com/HelloHQ/plugin-sdk/sdks/go
                (the tag is the version; there is no file to compare)

      Without this a tag of one version could publish the version in the file
      (crates.io, npm and PyPI never let a version be published twice). With
      --github-output, appends lang= and version= for later steps.

Exit status 1 with a `::error::` line on any problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

GO_MODULE = "github.com/HelloHQ/plugin-sdk/sdks/go"
PACKAGES = {
    "rust": ("sdks/rust/Cargo.toml", "hellohq-plugin-sdk"),
    "js": ("sdks/js/package.json", "@hellohq/plugin-sdk"),
    "python": ("sdks/python/pyproject.toml", "hellohq-plugin-sdk"),
    "go": ("sdks/go/go.mod", GO_MODULE),
}

# sdks/<lang>/v<MAJOR.MINOR.PATCH> with an optional pre-release suffix written
# the way that registry spells it (-rc.1 for cargo and npm, rc1 for PyPI).
TAG_RE = re.compile(r"sdks/(go|rust|js|python)/v(\d+\.\d+\.\d+(?:[-.]?[0-9A-Za-z][0-9A-Za-z.]*)?)")


class ReleaseError(Exception):
    pass


def parse_tag(tag: str) -> tuple[str, str]:
    m = TAG_RE.fullmatch(tag)
    if not m:
        raise ReleaseError(
            f"tag {tag!r} is not sdks/<go|rust|js|python>/v<MAJOR.MINOR.PATCH>[suffix]"
        )
    return m.group(1), m.group(2)


def package_identity(lang: str, root: Path) -> tuple[str, str | None]:
    """(name, version) from the SDK's own package file; version is None for Go."""
    rel, _ = PACKAGES[lang]
    path = root / rel
    if not path.is_file():
        raise ReleaseError(f"{rel} is missing")
    text = path.read_text(encoding="utf-8")
    if lang == "go":
        m = re.search(r"^module\s+(\S+)\s*$", text, re.MULTILINE)
        if not m:
            raise ReleaseError(f"{rel} has no module line")
        return m.group(1), None
    if lang == "js":
        data = json.loads(text)
        table = data
    else:
        data = tomllib.loads(text)
        table = data.get("package" if lang == "rust" else "project", {})
    name, version = table.get("name"), table.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise ReleaseError(f"{rel} has no literal name and version to publish")
    return name, version


def check(tag: str, root: Path) -> tuple[str, str]:
    lang, version = parse_tag(tag)
    rel, want_name = PACKAGES[lang]
    name, have = package_identity(lang, root)
    if name != want_name:
        raise ReleaseError(f"{rel} is named {name!r}; this release publishes {want_name!r}")
    if have is not None and have != version:
        raise ReleaseError(
            f"tag {tag} is version {version} but {rel} says {have}. "
            "Bump the file, merge it, then tag that commit."
        )
    return lang, version


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--tag", required=True)
    c.add_argument("--root", default=".")
    c.add_argument("--github-output")
    args = ap.parse_args(argv)
    try:
        lang, version = check(args.tag, Path(args.root))
    except ReleaseError as e:
        print(f"::error::{e}")
        return 1
    print(f"ok: {lang} {version}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"lang={lang}\nversion={version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
