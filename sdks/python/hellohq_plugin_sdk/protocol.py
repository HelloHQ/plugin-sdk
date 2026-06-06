"""Protocol constants for the Tier 1 sidecar wire format.

Mirrors HelloHQ/plugin-protocol/sidecar/{lifecycle,envelope}.schema.json.
"""

from __future__ import annotations

#: The hellohq:plugin protocol version this SDK targets.
PROTOCOL_VERSION = "1.0.0"

# Lifecycle message types (carry a "type" field).
TYPE_READY = "ready"
TYPE_SHUTDOWN = "shutdown"
TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_EVENT = "event"

# Error codes for the RPC error-response envelope.
ERR_INVALID_INPUT = "invalid_input"
ERR_EXECUTION_FAILED = "execution_failed"
ERR_UNSUPPORTED_FUNCTION = "unsupported_function"
ERR_TIMEOUT = "timeout"


class PluginError(Exception):
    """Raise from a dispatch function to return a structured RPC error.

    The ``code`` is surfaced verbatim to the host; the message is shown in the
    plugin console (keep it short, never a raw traceback).
    """

    def __init__(self, message: str, code: str = ERR_EXECUTION_FAILED) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedFunction(PluginError):
    def __init__(self, function: str) -> None:
        super().__init__(f"unsupported function: {function}", ERR_UNSUPPORTED_FUNCTION)
