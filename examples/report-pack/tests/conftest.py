"""Make ``plugin`` and the in-repo Python SDK importable without a pip install.

Mirrors what ``hqplugin test --sidecar`` does (it adds ``sdks/python`` to
PYTHONPATH when it finds it by walking up from the plugin directory).
"""

from __future__ import annotations

import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
SDK_DIR = EXAMPLE_DIR.parent.parent / "sdks" / "python"

for path in (EXAMPLE_DIR, SDK_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
