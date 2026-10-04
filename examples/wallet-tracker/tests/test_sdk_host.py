import pytest
from hellohq_plugin_sdk import PluginError
from hellohq_plugin_sdk import host as sdk

from wallet_tracker.errors import FetchError, HostUnsupported
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


def test_propose_is_explicitly_unsupported():
    with pytest.raises(HostUnsupported):
        SdkHost().propose({"schema": "x", "proposals": []})
