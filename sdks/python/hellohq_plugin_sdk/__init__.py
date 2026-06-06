"""HelloHQ Tier 1 (Python) plugin SDK."""

from __future__ import annotations

from .protocol import (
    PROTOCOL_VERSION,
    PluginError,
    UnsupportedFunction,
)
from .sidecar import Dispatch, emit_event, serve

__all__ = [
    "PROTOCOL_VERSION",
    "PluginError",
    "UnsupportedFunction",
    "Dispatch",
    "serve",
    "emit_event",
]
