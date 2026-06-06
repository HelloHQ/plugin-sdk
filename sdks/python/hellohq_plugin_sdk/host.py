"""Host API calls for Tier 1 (sidecar) Python plugins.

Plugins running in the HelloHQ Tier-1 sidecar communicate with the host via
the NDJSON pipe.  This module exposes synchronous host calls that can be
issued from inside a dispatch handler.

Wire protocol (ai:inference)
----------------------------
The sidecar's dispatch function is running on the main thread.  To call the
host AI backend the plugin:

1. Writes one NDJSON line to **stdout**::

       {"type":"ai_complete","seq":N,"messages":[…],"opts":{…}}

2. Blocks on a single **stdin** ``readline()`` — the Dart host processes the
   request asynchronously and writes the response before resuming normal
   message delivery::

       {"type":"ai_response","seq":N,"content":"…","usage":{…}}

   or on error::

       {"type":"ai_response","seq":N,"error":"…","error_code":"…"}

This "stop-the-world" RPC works because the sidecar loop is deliberately
single-threaded: the Dart host never sends another stdin message while waiting
for the dispatch result.
"""

from __future__ import annotations

import json
import sys
from itertools import count
from typing import Any

from .protocol import (
    ERR_EXECUTION_FAILED,
    TYPE_AI_COMPLETE,
    TYPE_AI_RESPONSE,
    PluginError,
)

_seq_counter = count()


def ai_complete(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    temperature: float | None = None,
    tools: list[dict] | None = None,
) -> dict[str, Any]:
    """Call the host AI backend and return the response data dict.

    The host routes the request through the user's configured BYOK backend —
    the plugin never sees the API key.  Requires ``ai:inference`` in the plugin
    manifest permissions.

    Args:
        messages: List of ``{"role": "user"|"assistant", "content": "…"}`` dicts.
        max_tokens: Maximum tokens to generate (default 512).
        temperature: Sampling temperature (0–1). Omit for the model default.
        tools: Optional tool definitions for tool-use / function-calling.

    Returns:
        ``{"content": "…", "input_tokens": N, "output_tokens": N, "model": "…"}``

    Raises:
        PluginError: If the host denies the request or inference fails.
    """
    seq = next(_seq_counter)
    opts: dict[str, Any] = {"max_tokens": max_tokens}
    if temperature is not None:
        opts["temperature"] = temperature

    msg: dict[str, Any] = {
        "type": TYPE_AI_COMPLETE,
        "seq": seq,
        "messages": messages,
        "opts": opts,
    }
    if tools:
        msg["tools"] = tools

    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

    line = sys.stdin.readline()
    if not line:
        raise PluginError(
            "ai_complete: host closed stdin without responding",
            ERR_EXECUTION_FAILED,
        )

    result = json.loads(line)

    if result.get("type") != TYPE_AI_RESPONSE or result.get("seq") != seq:
        raise PluginError(
            f"ai_complete: unexpected host response: {line[:120]}",
            ERR_EXECUTION_FAILED,
        )

    if "error" in result:
        raise PluginError(
            result.get("error", "ai inference failed"),
            result.get("error_code", ERR_EXECUTION_FAILED),
        )

    return {
        "content": result.get("content", ""),
        "input_tokens": result.get("usage", {}).get("input_tokens", 0),
        "output_tokens": result.get("usage", {}).get("output_tokens", 0),
        "model": result.get("usage", {}).get("model", ""),
    }
