import sys

import pytest
from hellohq_plugin_sdk import PluginError
from hellohq_plugin_sdk import host as sdk
from hellohq_plugin_sdk.proposals import (
    Outcome,
    ProposeBadRequest,
    ProposeError,
    ProposePermissionDenied,
    ProposeQuotaExceeded,
    ProposeRateLimited,
    ProposeUnsupported,
    Receipt,
)

from wallet_tracker.errors import FetchError, HostError, HostUnsupported, ProposeRefused
from wallet_tracker.sdk_host import SdkHost


def test_fetch_maps_sdk_response(monkeypatch):
    seen = {}

    def fake_fetch(url, *, method, headers, body):
        seen.update(url=url, method=method, headers=headers, body=body)
        return {"status": 200, "headers": {"Content-Type": "application/json"}, "body": "{}"}

    monkeypatch.setattr(sdk, "fetch", fake_fetch)
    resp = SdkHost().fetch("POST", "https://x.example/", {"Accept": "a"}, "b")
    assert (resp.status, resp.body, resp.header("content-type")) == (200, "{}", "application/json")
    assert seen == {"url": "https://x.example/", "method": "POST", "headers": {"Accept": "a"}, "body": "b"}


def test_fetch_maps_plugin_error_with_code(monkeypatch):
    def boom(*_a, **_k):
        raise PluginError("denied", "permission_denied")

    monkeypatch.setattr(sdk, "fetch", boom)
    with pytest.raises(FetchError) as err:
        SdkHost().fetch("GET", "https://x.example/")
    assert err.value.code == "permission_denied"


BATCH = {"schema": "hellohq.proposal-batch@1", "proposals": [{"kind": "valuation"}]}


def test_propose_maps_sdk_receipts_to_plain_dicts(monkeypatch):
    seen = []

    def fake_propose(batch):
        seen.append(batch)
        return [Receipt(0, Outcome.QUEUED), Receipt(1, Outcome.INVALID, "bad_value")]

    monkeypatch.setattr(sdk, "propose", fake_propose)
    out = SdkHost().propose(BATCH)
    assert seen == [BATCH]
    assert out == [{"index": 0, "outcome": "queued"}, {"index": 1, "outcome": "invalid", "reason": "bad_value"}]


def test_propose_unknown_method_is_host_unsupported(monkeypatch):
    def refuse(_batch):
        raise ProposeUnsupported("unknown_method:propose", "unknown_method")

    monkeypatch.setattr(sdk, "propose", refuse)
    with pytest.raises(HostUnsupported):
        SdkHost().propose(BATCH)


@pytest.mark.parametrize(
    "error,code,reason,retryable",
    [
        (ProposePermissionDenied("no grant", "permission_denied"), "permission_denied", None, False),
        (ProposeRateLimited("slow down", "rate_limit_exceeded"), "rate_limit_exceeded", None, True),
        (ProposeQuotaExceeded("full", "quota_exceeded"), "quota_exceeded", None, True),
        (ProposeBadRequest("bad", "bad_request", "host_field_supplied"), "bad_request", "host_field_supplied", False),
        (ProposeError("odd", "future_code"), "future_code", None, False),
    ],
)
def test_propose_refusals_keep_the_hosts_code(monkeypatch, error, code, reason, retryable):
    def refuse(_batch):
        raise error

    monkeypatch.setattr(sdk, "propose", refuse)
    with pytest.raises(ProposeRefused) as err:
        SdkHost().propose(BATCH)
    assert (err.value.code, err.value.reason, err.value.retryable) == (code, reason, retryable)


def test_propose_other_plugin_errors_become_host_errors(monkeypatch):
    def broken(_batch):
        raise PluginError("propose: host closed stdin without responding", "execution_failed")

    monkeypatch.setattr(sdk, "propose", broken)
    with pytest.raises(HostError) as err:
        SdkHost().propose(BATCH)
    assert not isinstance(err.value, ProposeRefused | HostUnsupported)
    assert err.value.code == "execution_failed"


def test_an_sdk_without_propose_is_host_unsupported(monkeypatch):
    # hellohq-plugin-sdk < 0.2.0 has no hellohq_plugin_sdk.proposals.
    monkeypatch.setitem(sys.modules, "hellohq_plugin_sdk.proposals", None)
    with pytest.raises(HostUnsupported, match="needs >= 0.2.0"):
        SdkHost().propose(BATCH)


def test_fetch_refuses_a_binary_body(monkeypatch):
    # The SDK returns bytes when the host sent the body base64 (not UTF-8).
    def fake_fetch(url, *, method, headers, body):
        return {"status": 200, "headers": {}, "body": b"\xff", "body_bytes": b"\xff", "body_encoding": "base64"}

    monkeypatch.setattr(sdk, "fetch", fake_fetch)
    with pytest.raises(FetchError) as err:
        SdkHost().fetch("GET", "https://x.example/")
    assert err.value.code == "unexpected_response"
