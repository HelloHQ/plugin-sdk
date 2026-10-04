import pytest
from hellohq_plugin_sdk import PluginError, UnsupportedFunction

import plugin
from conftest import FakeHost, ok
from fixtures import BTC_P2WPKH, address_response


def test_dispatch_routes_scan_with_a_fake_host(monkeypatch):
    host = FakeHost(
        lambda m, u, b: ok(address_response(BTC_P2WPKH)) if "/address/" in u else None, supports_propose=False
    )
    monkeypatch.setattr(plugin, "SdkHost", lambda: host)
    import wallet_tracker.tracker as tracker

    monkeypatch.setattr(tracker, "SystemClock", lambda: __import__("conftest").FakeClock())
    out = plugin.dispatch(
        "run", {"input": {"function": "scan", "args": {"btc_addresses": [BTC_P2WPKH], "price_currency": None}}}
    )
    assert out["submission"]["status"] == "host_unsupported" and out["proposals"]


def test_dispatch_errors():
    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", {"input": {"function": "nope"}})
    with pytest.raises(PluginError) as err:
        plugin.dispatch("run", {"input": {"function": "scan", "args": {"btc_addresses": "x"}}})
    assert err.value.code == "invalid_input"
