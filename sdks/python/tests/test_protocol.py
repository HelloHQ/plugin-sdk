"""Protocol constant + error-type tests for the Tier-1 Python SDK."""

from __future__ import annotations

import hellohq_plugin_sdk.protocol as protocol
from hellohq_plugin_sdk import PluginError, UnsupportedFunction


def test_protocol_version() -> None:
    assert protocol.PROTOCOL_VERSION == "1.0.0"


def test_lifecycle_message_types() -> None:
    assert protocol.TYPE_READY == "ready"
    assert protocol.TYPE_SHUTDOWN == "shutdown"
    assert protocol.TYPE_PING == "ping"
    assert protocol.TYPE_PONG == "pong"
    assert protocol.TYPE_EVENT == "event"


def test_host_call_message_types() -> None:
    assert protocol.TYPE_AI_COMPLETE == "ai_complete"
    assert protocol.TYPE_AI_RESPONSE == "ai_response"
    assert protocol.TYPE_STORAGE_GET == "storage_get"
    assert protocol.TYPE_STORAGE_SET == "storage_set"
    assert protocol.TYPE_STORAGE_DELETE == "storage_delete"
    assert protocol.TYPE_STORAGE_RESPONSE == "storage_response"
    assert protocol.TYPE_HTTP_REQUEST == "http_request"
    assert protocol.TYPE_HTTP_RESPONSE == "http_response"


def test_error_codes() -> None:
    assert protocol.ERR_INVALID_INPUT == "invalid_input"
    assert protocol.ERR_EXECUTION_FAILED == "execution_failed"
    assert protocol.ERR_UNSUPPORTED_FUNCTION == "unsupported_function"
    assert protocol.ERR_TIMEOUT == "timeout"


def test_plugin_error_carries_code() -> None:
    err = PluginError("boom", protocol.ERR_INVALID_INPUT)
    assert err.message == "boom"
    assert err.code == "invalid_input"


def test_unsupported_function() -> None:
    err = UnsupportedFunction("frobnicate")
    assert err.code == protocol.ERR_UNSUPPORTED_FUNCTION
    assert "frobnicate" in err.message
