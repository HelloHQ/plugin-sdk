"""HelloHQ Tier 1 (Python) plugin SDK."""

from __future__ import annotations

from . import host
from .proposals import (
    Holding,
    Instrument,
    Money,
    Outcome,
    ProposalBatch,
    ProposeError,
    ProposePermissionDenied,
    ProposeUnsupported,
    Quantity,
    Receipt,
    Source,
    Valuation,
)
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
    "host",
    # Propose-only writes (see hellohq_plugin_sdk.proposals for the full set)
    "Holding",
    "Valuation",
    "ProposalBatch",
    "Money",
    "Quantity",
    "Instrument",
    "Source",
    "Receipt",
    "Outcome",
    "ProposeError",
    "ProposePermissionDenied",
    "ProposeUnsupported",
]
