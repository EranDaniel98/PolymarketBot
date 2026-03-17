import pytest
from polymarket_bot.signals.base import SignalPlugin
from polymarket_bot.models import Market, Signal
from datetime import datetime, timezone


async def test_signal_plugin_is_abstract():
    with pytest.raises(TypeError):
        SignalPlugin()


class DummyPlugin(SignalPlugin):
    async def start(self): pass
    async def stop(self): pass
    async def evaluate(self, market):
        return None

    @property
    def name(self) -> str:
        return "dummy"


async def test_concrete_plugin_can_be_instantiated():
    plugin = DummyPlugin()
    assert plugin.name == "dummy"
