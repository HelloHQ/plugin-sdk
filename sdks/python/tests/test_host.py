"""Wire-format tests for the synchronous host calls (ai/storage/network).

Each call writes one NDJSON request to stdout then blocks on a stdin readline.
We replace stdout/stdin with in-memory buffers and reset the seq counter so the
canned response's seq matches the request's.
"""

from __future__ import annotations

import io
import itertools
import json
import sys

import pytest

import hellohq_plugin_sdk.host as host
from hellohq_plugin_sdk import PluginError


def _run(fn, response, monkeypatch):
    """Run host call *fn* with a single canned *response* dict on stdin.

    Returns (result, sent_request_dict).
    """
    # Predictable seq: first call uses seq 0.
    monkeypatch.setattr(host, "_seq_counter", itertools.count())
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(response) + "\n"))
    result = fn()
    sent = json.loads(out.getvalue().strip().splitlines()[0])
    return result, sent


def test_ai_complete(monkeypatch):
    resp = {
        "type": "ai_response",
        "seq": 0,
        "content": "hello",
        "usage": {"input_tokens": 3, "output_tokens": 1, "model": "m"},
    }
    result, sent = _run(
        lambda: host.ai_complete(
            [{"role": "user", "content": "x"}], max_tokens=10, temperature=0.2
        ),
        resp,
        monkeypatch,
    )
    assert sent["type"] == "ai_complete"
    assert sent["seq"] == 0
    assert sent["opts"]["max_tokens"] == 10
    assert sent["opts"]["temperature"] == 0.2
    assert result["content"] == "hello"
    assert result["input_tokens"] == 3
    assert result["output_tokens"] == 1
    assert result["model"] == "m"


def test_ai_complete_with_tools(monkeypatch):
    tools = [{"name": "done", "description": "d", "input_schema": {}}]
    resp = {"type": "ai_response", "seq": 0, "content": "", "usage": {}}
    _, sent = _run(
        lambda: host.ai_complete([{"role": "user", "content": "x"}], tools=tools),
        resp,
        monkeypatch,
    )
    assert sent["tools"] == tools


def test_ai_complete_error_raises(monkeypatch):
    resp = {
        "type": "ai_response",
        "seq": 0,
        "error": "budget exceeded",
        "error_code": "execution_failed",
    }
    with pytest.raises(PluginError):
        _run(lambda: host.ai_complete([{"role": "user", "content": "x"}]), resp, monkeypatch)


def test_storage_get_hit(monkeypatch):
    resp = {"type": "storage_response", "seq": 0, "value": "v"}
    result, sent = _run(lambda: host.storage_get("k"), resp, monkeypatch)
    assert sent["type"] == "storage_get"
    assert sent["key"] == "k"
    assert result == "v"


def test_storage_get_miss(monkeypatch):
    resp = {"type": "storage_response", "seq": 0, "value": None}
    result, _ = _run(lambda: host.storage_get("absent"), resp, monkeypatch)
    assert result is None


def test_storage_set(monkeypatch):
    resp = {"type": "storage_response", "seq": 0, "ok": True}
    result, sent = _run(lambda: host.storage_set("k", "v"), resp, monkeypatch)
    assert sent["type"] == "storage_set"
    assert sent["key"] == "k"
    assert sent["value"] == "v"
    assert result is None


def test_storage_delete(monkeypatch):
    resp = {"type": "storage_response", "seq": 0, "deleted": 1}
    result, sent = _run(lambda: host.storage_delete("k"), resp, monkeypatch)
    assert sent["type"] == "storage_delete"
    assert result == 1


def test_storage_denied_raises(monkeypatch):
    resp = {
        "type": "storage_response",
        "seq": 0,
        "error": "denied:plugin:storage",
        "error_code": "permission_denied",
    }
    with pytest.raises(PluginError):
        _run(lambda: host.storage_get("k"), resp, monkeypatch)


def test_fetch(monkeypatch):
    resp = {
        "type": "http_response",
        "seq": 0,
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body": "{}",
    }
    result, sent = _run(
        lambda: host.fetch(
            "https://api.example.com/x",
            method="POST",
            headers={"authorization": "Bearer t"},
            body='{"q":1}',
        ),
        resp,
        monkeypatch,
    )
    assert sent["type"] == "http_request"
    assert sent["method"] == "POST"
    assert sent["url"] == "https://api.example.com/x"
    assert sent["headers"]["authorization"] == "Bearer t"
    assert sent["body"] == '{"q":1}'
    assert result["status"] == 200
    assert result["headers"]["content-type"] == "application/json"
    assert result["body"] == "{}"


def test_fetch_denied_raises(monkeypatch):
    resp = {
        "type": "http_response",
        "seq": 0,
        "error": "denied:network:fetch",
        "error_code": "permission_denied",
    }
    with pytest.raises(PluginError):
        _run(lambda: host.fetch("https://x.example.com"), resp, monkeypatch)


def test_unexpected_response_type_raises(monkeypatch):
    resp = {"type": "wrong_type", "seq": 0, "value": "v"}
    with pytest.raises(PluginError):
        _run(lambda: host.storage_get("k"), resp, monkeypatch)
