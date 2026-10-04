import socket
import sys
from pathlib import Path

import pytest

# The adapter (plugin.py) imports the sidecar SDK from this monorepo; the pure core does not.
SDK = Path(__file__).resolve().parents[3] / "sdks" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests must never touch the network: any socket connect fails the test."""

    def guard(*_a, **_k):
        raise AssertionError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
