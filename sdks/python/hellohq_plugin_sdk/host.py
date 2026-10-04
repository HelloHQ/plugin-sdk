"""Host API calls for Tier 1 (sidecar) Python plugins.

Plugins running in the HelloHQ Tier-1 sidecar communicate with the host via
the NDJSON pipe.  This module exposes synchronous host calls that can be
issued from inside a dispatch handler.

All host calls follow the same "stop-the-world" RPC pattern:

1. Plugin writes one NDJSON line to **stdout** with a unique ``seq`` counter.
2. Plugin blocks on a single **stdin** ``readline()`` — the Dart host processes
   the request and writes the response before resuming normal message delivery.

This works because the sidecar loop is deliberately single-threaded: the Dart
host never sends another stdin message while waiting for the dispatch result.

Wire protocols
--------------
ai:inference (requires ai:inference permission)::

    plugin → host: {"type":"ai_complete","seq":N,"messages":[…],"opts":{…}}
    host → plugin: {"type":"ai_response","seq":N,"content":"…","usage":{…}}
    host → plugin: {"type":"ai_response","seq":N,"error":"…","error_code":"…"}

plugin:storage (requires plugin:storage permission, Tier 1 only)::

    plugin → host: {"type":"storage_get","seq":N,"key":"…"}
    host → plugin: {"type":"storage_response","seq":N,"value":"…"|null}

    plugin → host: {"type":"storage_set","seq":N,"key":"…","value":"…"}
    host → plugin: {"type":"storage_response","seq":N,"ok":true}

    plugin → host: {"type":"storage_delete","seq":N,"key":"…"}
    host → plugin: {"type":"storage_response","seq":N,"deleted":0|1}

network:fetch (requires network:fetch permission, Verified tier, Tier 1 only)::

    plugin → host: {"type":"http_request","seq":N,"method":"GET","url":"…",
                    "headers":{},"body":""}
    host → plugin: {"type":"http_response","seq":N,"status":200,
                    "headers":{},"body":"…"}
    host → plugin: {"type":"http_response","seq":N,"status":200,
                    "headers":{},"body":"<base64>","body_encoding":"base64"}
    host → plugin: {"type":"http_response","seq":N,"error":"…","error_code":"…"}

    ``body_encoding`` is absent (meaning ``"utf8"``) when the response bytes are
    valid UTF-8, and ``"base64"`` otherwise. :func:`fetch` decodes it.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from itertools import count
from typing import Any, TypedDict

from .protocol import (
    ERR_EXECUTION_FAILED,
    TYPE_AI_COMPLETE,
    TYPE_AI_RESPONSE,
    TYPE_HTTP_REQUEST,
    TYPE_HTTP_RESPONSE,
    TYPE_STORAGE_DELETE,
    TYPE_STORAGE_GET,
    TYPE_STORAGE_RESPONSE,
    TYPE_STORAGE_SET,
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


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────────────────────


def _rpc(msg: dict[str, Any], expected_type: str) -> dict[str, Any]:
    """Send *msg* to the host and return the parsed response.

    Raises :exc:`PluginError` on host error or unexpected response type.
    """
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

    line = sys.stdin.readline()
    if not line:
        raise PluginError(
            f"{msg['type']}: host closed stdin without responding",
            ERR_EXECUTION_FAILED,
        )

    result = json.loads(line)

    if result.get("type") != expected_type or result.get("seq") != msg["seq"]:
        raise PluginError(
            f"{msg['type']}: unexpected host response: {line[:120]}",
            ERR_EXECUTION_FAILED,
        )

    if "error" in result:
        raise PluginError(
            result.get("error", "host call failed"),
            result.get("error_code", ERR_EXECUTION_FAILED),
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Storage (plugin:storage permission, Tier 1 only)
# ─────────────────────────────────────────────────────────────────────────────


def storage_get(key: str) -> str | None:
    """Read a value from the plugin's persistent key-value store.

    Storage is sandboxed to this plugin — no other plugin or the host can read it.

    Args:
        key: The key to look up (max 255 bytes, printable ASCII).

    Returns:
        The stored string value, or ``None`` if the key is absent.

    Raises:
        PluginError: If the host denies the request (missing ``plugin:storage``
            permission) or an I/O error occurs.
    """
    seq = next(_seq_counter)
    result = _rpc(
        {"type": TYPE_STORAGE_GET, "seq": seq, "key": key},
        TYPE_STORAGE_RESPONSE,
    )
    return result.get("value")  # None when the key is absent


def storage_set(key: str, value: str) -> None:
    """Write a value to the plugin's persistent key-value store.

    Values are stored as UTF-8 strings.  The host enforces a per-plugin quota;
    exceeding it raises :exc:`PluginError`.

    Args:
        key: The key to write (max 255 bytes, printable ASCII).
        value: The value to store (UTF-8 string, max 64 KiB).

    Raises:
        PluginError: If the host denies the request or the quota is exceeded.
    """
    seq = next(_seq_counter)
    _rpc(
        {"type": TYPE_STORAGE_SET, "seq": seq, "key": key, "value": value},
        TYPE_STORAGE_RESPONSE,
    )


def storage_delete(key: str) -> int:
    """Delete a key from the plugin's persistent key-value store.

    Args:
        key: The key to delete.

    Returns:
        ``1`` if the key existed and was deleted, ``0`` if it was not present.

    Raises:
        PluginError: If the host denies the request.
    """
    seq = next(_seq_counter)
    result = _rpc(
        {"type": TYPE_STORAGE_DELETE, "seq": seq, "key": key},
        TYPE_STORAGE_RESPONSE,
    )
    return int(result.get("deleted", 0))


# ─────────────────────────────────────────────────────────────────────────────
# Network (network:fetch permission, Verified tier, Tier 1 only)
# ─────────────────────────────────────────────────────────────────────────────


#: Value of ``body_encoding`` for a text body (also what an absent field means).
BODY_ENCODING_UTF8 = "utf8"
#: Value of ``body_encoding`` for a body the host sent as base64.
BODY_ENCODING_BASE64 = "base64"

#: Request headers the host forwards; every other header is dropped by the host.
ALLOWED_REQUEST_HEADERS = frozenset(
    {"accept", "accept-language", "content-type", "if-none-match", "if-modified-since"}
)


class FetchResponse(TypedDict):
    """What :func:`fetch` returns. A plain ``dict`` at runtime."""

    #: Upstream HTTP status code (a 4xx/5xx or an unfollowed 3xx is not an error).
    status: int
    #: Upstream response headers (``Set-Cookie`` already stripped by the host).
    headers: dict[str, str]
    #: ``str`` when the response was UTF-8 text (unchanged from earlier SDKs);
    #: ``bytes`` when it was not (the host sent it base64; this is decoded).
    body: str | bytes
    #: The exact response bytes, whatever ``body_encoding`` was.
    body_bytes: bytes
    #: ``"utf8"`` or ``"base64"``: how the host sent the body.
    body_encoding: str


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str = "",
) -> FetchResponse:
    """Make an outbound HTTP request through the host.

    The plugin never opens a socket directly — all network traffic routes
    through the host so it can enforce ``network:fetch`` permission and the
    origin allowlist the manifest declares on that permission
    (``scope.origins``). Requires ``network:fetch`` in the plugin manifest and
    a Verified plugin tier. HTTPS only; redirects are not followed (a 3xx comes
    back as the response).

    Request headers: the host forwards only ``Accept``, ``Accept-Language``,
    ``Content-Type``, ``If-None-Match`` and ``If-Modified-Since``
    (:data:`ALLOWED_REQUEST_HEADERS`) and silently drops every other header,
    including ``Authorization``, ``Cookie`` and ``User-Agent``. Credentials are
    host-only.

    Response body: the host sends a body that is valid UTF-8 as text and
    anything else as base64 with ``body_encoding: "base64"``. This function
    decodes it, so:

    * ``body`` is a ``str`` for a text response, exactly as before, and
      ``bytes`` for a binary one (a PDF, an image, Latin-1 text);
    * ``body_bytes`` is always the exact response bytes, for code that wants
      one type;
    * ``body_encoding`` says which case it was.

    Args:
        url: The fully-qualified ``https://`` URL to request.
        method: HTTP method (default ``"GET"``).
        headers: Optional request headers (see the allowlist above).
        body: Optional request body text, sent as UTF-8 (``""`` for none).
            A body on GET/HEAD is refused by the host.

    Returns:
        A :class:`FetchResponse` dict:
        ``{"status": 200, "headers": {...}, "body": "…" | b"…",
        "body_bytes": b"…", "body_encoding": "utf8" | "base64"}``.

    Raises:
        PluginError: If the host denies the request, the origin is not
            allowed, a network error occurs (``code`` is the host's
            ``error_code``; the message never contains the URL), or the host
            reply carries a ``body_encoding`` this SDK does not understand.
    """
    seq = next(_seq_counter)
    result = _rpc(
        {
            "type": TYPE_HTTP_REQUEST,
            "seq": seq,
            "method": method,
            "url": url,
            "headers": headers or {},
            "body": body,
        },
        TYPE_HTTP_RESPONSE,
    )
    encoding = result.get("body_encoding")
    if encoding is None:  # absent: a text body, the wire before body_encoding existed
        encoding = BODY_ENCODING_UTF8
    decoded, raw = _decode_body(result.get("body", ""), encoding)
    return {
        "status": result.get("status", 0),
        "headers": result.get("headers", {}),
        "body": decoded,
        "body_bytes": raw,
        "body_encoding": encoding,
    }


def _decode_body(wire: Any, encoding: Any) -> tuple[str | bytes, bytes]:
    """Decode an ``http_response`` body per its ``body_encoding``.

    Returns ``(body, body_bytes)``: ``body`` is the text for ``"utf8"`` and the
    decoded bytes for ``"base64"``.
    """
    if wire is None:
        wire = ""
    if not isinstance(wire, str):
        raise PluginError(
            f"fetch: host sent a non-string body ({type(wire).__name__})",
            ERR_EXECUTION_FAILED,
        )
    if encoding == BODY_ENCODING_UTF8:
        return wire, wire.encode("utf-8")
    if encoding == BODY_ENCODING_BASE64:
        try:
            raw = base64.b64decode(wire, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PluginError(
                "fetch: host sent body_encoding 'base64' but the body is not valid base64",
                ERR_EXECUTION_FAILED,
            ) from exc
        return raw, raw
    raise PluginError(
        f"fetch: unsupported body_encoding {encoding!r} from host "
        f"(this SDK understands {BODY_ENCODING_UTF8!r} and {BODY_ENCODING_BASE64!r}; "
        "upgrade hellohq-plugin-sdk)",
        ERR_EXECUTION_FAILED,
    )
