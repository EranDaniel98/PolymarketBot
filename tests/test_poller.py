import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from polymarket_bot.poller import SignalPoller
from polymarket_bot.scanner import MarketScanner
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Market, Signal, Direction


class FakePlugin:
    def __init__(self, name: str, signal: Signal | None):
        self._name = name
        self._signal = signal

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self, market):
        return self._signal


@pytest.fixture
def market():
    return Market(
        id="m1", question="Test?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.50,
    )


@pytest.fixture
def signal(market):
    return Signal(
        source="test", market_id=market.id, direction=Direction.YES,
        confidence=0.8, reasoning="Test signal",
        timestamp=datetime.now(timezone.utc),
    )


async def test_poller_publishes_signals(market, signal):
    scanner = AsyncMock(spec=MarketScanner)
    scanner.fetch_active_markets.return_value = [market]

    bus = EventBus()
    received = []

    async def capture(event):
        received.append(event)

    bus.subscribe("signal", capture)

    plugin = FakePlugin("test", signal)
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )

    poller._markets = [market]
    poller._running = True
    # Run one evaluation cycle manually
    await poller._evaluate_loop_once(market, plugin)
    assert len(received) == 1
    assert received[0].signal.source == "test"


async def test_poller_skips_low_confidence(market):
    scanner = AsyncMock(spec=MarketScanner)
    weak_signal = Signal(
        source="test", market_id=market.id, direction=Direction.YES,
        confidence=0.05, reasoning="Weak",
        timestamp=datetime.now(timezone.utc),
    )

    bus = EventBus()
    received = []
    bus.subscribe("signal", lambda e: received.append(e))

    plugin = FakePlugin("test", weak_signal)
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )

    poller._markets = [market]
    poller._running = True
    await poller._evaluate_loop_once(market, plugin)
    assert len(received) == 0


async def test_poller_handles_plugin_returning_none(market):
    scanner = AsyncMock(spec=MarketScanner)
    bus = EventBus()
    received = []
    bus.subscribe("signal", lambda e: received.append(e))

    plugin = FakePlugin("test", None)
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )

    poller._markets = [market]
    poller._running = True
    await poller._evaluate_loop_once(market, plugin)
    assert len(received) == 0
