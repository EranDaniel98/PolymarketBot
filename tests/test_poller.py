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

    def can_evaluate(self, market) -> bool:
        return True

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


# ---------------------------------------------------------------------------
# _evaluate_cycle() — batch tests
# ---------------------------------------------------------------------------

async def test_poller_batches_signals_per_market(market, signal):
    """_evaluate_cycle() publishes one signal_batch event per market that has signals."""
    scanner = AsyncMock(spec=MarketScanner)
    bus = EventBus()
    batches = []

    async def capture(event):
        batches.append(event)

    bus.subscribe("signal_batch", capture)

    plugin = FakePlugin("test", signal)
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 1
    assert batches[0].market.id == market.id
    assert len(batches[0].signals) == 1
    assert batches[0].signals[0].source == "test"


async def test_poller_batch_groups_by_market(signal):
    """Signals from multiple plugins for the same market are grouped into one batch."""
    market1 = Market(
        id="m1", question="Q1?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.50,
    )
    market2 = Market(
        id="m2", question="Q2?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xc", "NO": "0xd"}, current_price=0.60,
    )
    sig1 = Signal(
        source="plugin_a", market_id="m1", direction=Direction.YES,
        confidence=0.8, reasoning="r", timestamp=datetime.now(timezone.utc),
    )
    sig2 = Signal(
        source="plugin_b", market_id="m1", direction=Direction.NO,
        confidence=0.7, reasoning="r", timestamp=datetime.now(timezone.utc),
    )
    sig3 = Signal(
        source="plugin_a", market_id="m2", direction=Direction.YES,
        confidence=0.9, reasoning="r", timestamp=datetime.now(timezone.utc),
    )

    scanner = AsyncMock(spec=MarketScanner)
    bus = EventBus()
    batches = []

    async def capture(event):
        batches.append(event)

    bus.subscribe("signal_batch", capture)

    plugin_a = FakePlugin("plugin_a", sig1)
    plugin_b = FakePlugin("plugin_b", sig2)

    async def eval_a(m):
        return sig1 if m.id == "m1" else sig3

    async def eval_b(m):
        return sig2 if m.id == "m1" else None

    plugin_a.evaluate = eval_a
    plugin_b.evaluate = eval_b

    poller = SignalPoller(
        scanner=scanner, plugins=[plugin_a, plugin_b], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market1, market2]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 2
    by_market = {b.market.id: b for b in batches}
    assert set(by_market.keys()) == {"m1", "m2"}
    assert len(by_market["m1"].signals) == 2
    assert len(by_market["m2"].signals) == 1


async def test_poller_batch_skips_markets_with_no_signals(market):
    """Markets where all plugins return None produce no batch event."""
    scanner = AsyncMock(spec=MarketScanner)
    bus = EventBus()
    batches = []

    async def capture(event):
        batches.append(event)

    bus.subscribe("signal_batch", capture)

    plugin = FakePlugin("test", None)  # always returns None
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 0


async def test_poller_batch_does_not_publish_individual_signals(market, signal):
    """_evaluate_cycle() must NOT publish 'signal' events, only 'signal_batch'."""
    scanner = AsyncMock(spec=MarketScanner)
    bus = EventBus()
    individual_signals = []
    batch_events = []

    async def cap_signal(event):
        individual_signals.append(event)

    async def cap_batch(event):
        batch_events.append(event)

    bus.subscribe("signal", cap_signal)
    bus.subscribe("signal_batch", cap_batch)

    plugin = FakePlugin("test", signal)
    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(individual_signals) == 0, "signal_batch must not emit individual 'signal' events"
    assert len(batch_events) == 1
