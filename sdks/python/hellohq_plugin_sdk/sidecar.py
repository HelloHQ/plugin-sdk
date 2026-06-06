"""Tier 1 sidecar runtime.

Implements the newline-delimited-JSON protocol the HelloHQ host speaks over
stdin/stdout, so plugin authors only write a ``dispatch`` function.

    from hellohq_plugin_sdk import serve, PluginError

    def dispatch(function: str, args):
        if function == "double":
            return {"result": args[0] * 2}
        raise PluginError(f"unknown function: {function}")

    serve(dispatch)

The loop is deliberately synchronous and single-threaded: the host sends one
request at a time and waits for its response before sending the next.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Optional, TextIO

from .protocol import (
    ERR_EXECUTION_FAILED,
    PROTOCOL_VERSION,
    TYPE_PING,
    TYPE_PONG,
    TYPE_READY,
    TYPE_SHUTDOWN,
    PluginError,
)

#: A plugin's request handler: ``(function_name, args) -> json-serialisable``.
Dispatch = Callable[[str, Any], Any]


def serve(
    dispatch: Dispatch,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> None:
    """Run the sidecar loop until a shutdown message or EOF.

    Sends ``ready`` immediately, then handles ``ping``/``shutdown`` lifecycle
    messages and RPC requests, routing each request to ``dispatch``.
    """
    inp = stdin or sys.stdin
    out = stdout or sys.stdout

    def send(obj: dict) -> None:
        out.write(json.dumps(obj, separators=(",", ":")))
        out.write("\n")
        out.flush()

    send({"type": TYPE_READY, "protocol_version": PROTOCOL_VERSION})

    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Unparseable line: ignore rather than crash the sidecar.
            continue

        msg_type = msg.get("type")
        if msg_type == TYPE_SHUTDOWN:
            out.flush()
            return
        if msg_type == TYPE_PING:
            send({"type": TYPE_PONG, "seq": msg.get("seq")})
            continue

        # RPC request (no "type", carries "id").
        req_id = msg.get("id")
        if req_id is None:
            continue
        try:
            result = dispatch(msg.get("function", ""), msg.get("args"))
            send({"id": req_id, "result": result})
        except PluginError as e:
            send({"id": req_id, "error": {"code": e.code, "message": e.message}})
        except Exception as e:  # noqa: BLE001 — never let a plugin bug kill the loop
            send(
                {
                    "id": req_id,
                    "error": {"code": ERR_EXECUTION_FAILED, "message": str(e)},
                }
            )


def emit_event(name: str, payload: Any, *, stdout: Optional[TextIO] = None) -> None:
    """Push a fire-and-forget event to the host (forwarded to a WebView).

    Only meaningful in ``webview`` UI mode; discarded otherwise.
    """
    out = stdout or sys.stdout
    out.write(json.dumps({"type": "event", "name": name, "payload": payload}))
    out.write("\n")
    out.flush()
