import pytest
from hellohq_plugin_sdk import PluginError, UnsupportedFunction

import plugin
from conftest import FakeClock, FakeHost


def test_dispatch_routes_context(monkeypatch):
    import macro_context.context as context

    monkeypatch.setattr(plugin, "SdkHost", lambda: FakeHost(lambda m, u, b: None))
    monkeypatch.setattr(context, "SystemClock", FakeClock)
    out = plugin.dispatch("run", {"input": {"function": "context", "args": {"sections": ["sgfx"]}}})
    assert out["schema"] == "hellohq.macro-context@1"
    assert out["series"] == [] and out["issues"][0]["code"] == "permission_denied"


def test_dispatch_errors():
    with pytest.raises(UnsupportedFunction):
        plugin.dispatch("run", {"input": {"function": "nope"}})
    with pytest.raises(PluginError) as err:
        plugin.dispatch("run", {"input": {"function": "context", "args": {"sections": ["x"]}}})
    assert err.value.code == "invalid_input"
