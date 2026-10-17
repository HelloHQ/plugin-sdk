# SPDX-License-Identifier: Apache-2.0
"""The streaming toolchain pins every source it builds by a full commit SHA.

A git revision written as a prefix (8 hex characters) is not a pin against a
repository an attacker controls: a 32-bit prefix can be brute-forced. SA4 in
docs/plugin/24 wants the exact commit, so every rev in the build files and the
ADR table must be 40 hex characters.

  python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = re.compile(r"^[0-9a-f]{40}$")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class ToolchainPins(unittest.TestCase):
    def test_dockerfile_revs_are_full_shas(self):
        revs = re.findall(r"^ARG (\w+_REV)=(\S+)", read("toolchain/streaming/Dockerfile"), re.M)
        self.assertGreaterEqual(len(revs), 2, "expected CJS_REV and WIT_BINDGEN_REV")
        for name, value in revs:
            self.assertRegex(value, FULL, f"{name} in the Dockerfile must be a full commit SHA")

    def test_mirror_inventory_revs_are_full_shas(self):
        inventory = re.findall(
            r'^\s*"(https://github\.com/[^|]+)\|([^|]+)\|([^"]+)"',
            read("toolchain/streaming/mirror-upstream.sh"),
            re.M,
        )
        self.assertGreaterEqual(len(inventory), 5)
        for url, _name, rev in inventory:
            self.assertRegex(rev, FULL, f"{url} is mirrored at {rev!r}, not a full commit SHA")

    def test_go_build_script_rev_is_a_full_sha(self):
        rev = re.search(r'^WIT_BINDGEN_REV="([^"]+)"', read("examples/component-quickstart-go/inference/build.sh"), re.M)
        self.assertIsNotNone(rev)
        self.assertRegex(rev.group(1), FULL)

    def test_the_same_wit_bindgen_rev_everywhere(self):
        docker = re.search(r"^ARG WIT_BINDGEN_REV=(\S+)", read("toolchain/streaming/Dockerfile"), re.M).group(1)
        build = re.search(r'^WIT_BINDGEN_REV="([^"]+)"', read("examples/component-quickstart-go/inference/build.sh"), re.M).group(1)
        mirror = re.search(r"wit-bindgen\|plugin-mirror-wit-bindgen\|([0-9a-f]+)", read("toolchain/streaming/mirror-upstream.sh")).group(1)
        self.assertEqual({docker, build, mirror}, {docker})

    def test_adr_table_names_no_short_revs(self):
        # `owner/repo`@`rev` entries in the pins table.
        short = re.findall(r"@`([0-9a-f]{7,39})`", read("docs/adr/0001-streaming-build-toolchain.md"))
        self.assertEqual(short, [], "docs/adr/0001 pins a short revision; write the full commit SHA")


if __name__ == "__main__":
    unittest.main()
