import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from polymarket_bot.exit_manager import ExitManager, ExitRule, TrackedPosition
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Direction


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def manager(bus):
    rules = ExitRule(take_profit=0.15, stop_loss=-0.10, trailing_stop=0.08)
    mgr = ExitManager(event_bus=bus, database=AsyncMock(), rules=rules)
    return mgr


def test_track_entry(manager):
    manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    assert "m1" in manager._positions
    assert manager._positions["m1"].entry_price == 0.40


def test_track_exit(manager):
    manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    manager.track_exit("m1")
    assert "m1" not in manager._positions


async def test_take_profit_triggers(manager):
    manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    # Price went up to 0.50 → 25% gain > 15% target
    manager.set_price_getter(lambda platform, mid: 0.50)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is not None
    assert "Take profit" in reason


async def test_stop_loss_triggers(manager):
    manager.track_entry("m1", Direction.YES, 0.50, 100.0)
    # Price dropped to 0.43 → -14% loss > -10% limit
    manager.set_price_getter(lambda platform, mid: 0.43)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is not None
    assert "Stop loss" in reason


async def test_no_exit_when_in_range(manager):
    manager.track_entry("m1", Direction.YES, 0.50, 100.0)
    # Price at 0.52 → +4%, within normal range
    manager.set_price_getter(lambda platform, mid: 0.52)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is None


async def test_trailing_stop(manager):
    manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    pos = manager._positions["m1"]
    # Simulate peak of 12% gain
    pos.peak_pnl_pct = 0.12

    # Now price pulled back → current pnl ~2.5%, trailing trigger = 12% - 8% = 4%
    manager.set_price_getter(lambda platform, mid: 0.41)

    reason = await manager._check_exit(pos)
    assert reason is not None
    assert "Trailing stop" in reason


async def test_no_exit_triggers(manager, bus):
    """Full exit flow: exit triggers a trade_decision event."""
    received = []

    async def capture(decision):
        received.append(decision)

    bus.subscribe("trade_decision", capture)

    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc),
    )

    await manager._trigger_exit(pos, "Test exit")
    assert len(received) == 1
    assert received[0].market_id == "m1"
    assert received[0].direction == Direction.NO  # Opposite of YES entry
