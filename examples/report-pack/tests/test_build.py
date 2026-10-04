"""The UI bundle ``build.sh`` produces (``ui.zip``) is what the host can load.

Skipped when ``ui.zip`` has not been built (``./build.sh`` needs Node and npm).
CI builds it first and sets ``REPORT_PACK_REQUIRE_BUILD=1`` so a missing
bundle fails instead of skipping.

What the host requires (docs/plugin/07; hellohq ``PluginBundleService``): an
``index.html`` entrypoint (or ``hq-bundle.json`` naming one), at most 20 MB
unzipped, no path traversal, and nothing the injected CSP blocks —
``default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; font-src 'self'; connect-src 'none'``.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from html.parser import HTMLParser

import pytest
from conftest import EXAMPLE_DIR

ZIP = EXAMPLE_DIR / "ui.zip"

if not ZIP.exists():
    if os.environ.get("REPORT_PACK_REQUIRE_BUILD"):
        raise AssertionError("ui.zip is missing: run ./build.sh first")
    pytest.skip("ui.zip not built (run ./build.sh)", allow_module_level=True)


def _read(name: str) -> str:
    with zipfile.ZipFile(ZIP) as z:
        return z.read(name).decode("utf-8")


def test_bundle_has_exactly_the_expected_files() -> None:
    with zipfile.ZipFile(ZIP) as z:
        infos = z.infolist()
    names = sorted(i.filename for i in infos)
    assert names == ["hq-bundle.json", "index.html", "styles.css", "ui.js"]
    for info in infos:
        assert not info.filename.startswith("/") and ".." not in info.filename
        assert info.date_time == (1980, 1, 1, 0, 0, 0)  # reproducible build
    assert sum(i.file_size for i in infos) < 20 * 1024 * 1024


def test_bundle_metadata_names_the_entrypoint() -> None:
    meta = json.loads(_read("hq-bundle.json"))
    assert meta["entrypoint"] == "index.html"


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []
        self.inline_script = False
        self._in_script = False
        self.handlers: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.handlers += [k for k in d if k.startswith("on")]
        if tag == "script":
            self.scripts.append(d)
            self._in_script = True
        if tag == "link":
            self.links.append(d)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline_script = True


def test_index_html_is_csp_safe_and_loads_only_bundled_files() -> None:
    page = _Page()
    page.feed(_read("index.html"))
    assert not page.inline_script, "inline <script> is blocked by script-src 'self'"
    assert not page.handlers, "inline event handlers are blocked by the CSP"
    assert [s.get("src") for s in page.scripts] == ["./ui.js"]
    assert [lk.get("href") for lk in page.links] == ["./styles.css"]


@pytest.mark.parametrize("name", ["index.html", "styles.css", "ui.js"])
def test_no_remote_resources_and_nothing_the_csp_blocks(name: str) -> None:
    text = _read(name)
    assert not re.search(r"(?i)\b(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}", text), name
    assert "@import" not in text
    for blocked in ("eval(", "new Function", "fetch(", "XMLHttpRequest", "WebSocket"):
        assert blocked not in text, (name, blocked)


def test_ui_script_uses_the_host_write_contract_and_never_innerhtml() -> None:
    js = _read("ui.js")
    assert "write_external" in js and "suggested_filename" in js
    assert "content_base64" in js
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert "localStorage" not in js and "sessionStorage" not in js
