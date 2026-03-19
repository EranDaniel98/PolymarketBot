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
def mock_db():
    db = AsyncMock()
    db.load_positions = AsyncMock(return_value=[])
    return db


@pytest.fixture
def manager(bus, mock_db):
    rules = ExitRule(take_profit=0.15, stop_loss=-0.10, trailing_stop=0.08)
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)
    return mgr


async def test_track_entry(manager):
    await manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    assert "m1" in manager._positions
    assert manager._positions["m1"].entry_price == 0.40


async def test_track_entry_with_tokens(manager):
    tokens = {"YES": "0xa", "NO": "0xb"}
    await manager.track_entry("m1", Direction.YES, 0.40, 100.0, tokens=tokens)
    assert manager._positions["m1"].tokens == tokens


async def test_track_exit(manager):
    await manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    await manager.track_exit("m1")
    assert "m1" not in manager._positions


async def test_take_profit_triggers(manager):
    await manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    # Price went up to 0.50 -> 25% gain > 15% target
    manager.set_price_getter(lambda platform, mid: 0.50)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is not None
    assert "Take profit" in reason


async def test_stop_loss_triggers(manager):
    await manager.track_entry("m1", Direction.YES, 0.50, 100.0)
    # Price dropped to 0.43 -> -14% loss > -10% limit
    manager.set_price_getter(lambda platform, mid: 0.43)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is not None
    assert "Stop loss" in reason


async def test_no_exit_when_in_range(manager):
    await manager.track_entry("m1", Direction.YES, 0.50, 100.0)
    # Price at 0.52 -> +4%, within normal range
    manager.set_price_getter(lambda platform, mid: 0.52)

    reason = await manager._check_exit(manager._positions["m1"])
    assert reason is None


async def test_trailing_stop(manager):
    await manager.track_entry("m1", Direction.YES, 0.40, 100.0)
    pos = manager._positions["m1"]
    # Simulate peak of 12% gain
    pos.peak_pnl_pct = 0.12

    # Now price pulled back -> current pnl ~2.5%, trailing trigger = 12% - 8% = 4%
    manager.set_price_getter(lambda platform, mid: 0.41)

    reason = await manager._check_exit(pos)
    assert reason is not None
    assert "Trailing stop" in reason


async def test_exit_triggers_same_direction(manager, bus):
    """Exit should use SAME direction with is_exit=True, not flip direction."""
    received = []

    async def capture(decision):
        received.append(decision)

    bus.subscribe("trade_decision", capture)

    tokens = {"YES": "0xa", "NO": "0xb"}
    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc),
        tokens=tokens,
    )

    await manager._trigger_exit(pos, "Test exit")
    assert len(received) == 1
    assert received[0].market_id == "m1"
    assert received[0].direction == Direction.YES  # Same direction as entry
    assert received[0].is_exit is True
    assert received[0].tokens == tokens


async def test_load_from_db(manager, mock_db):
    mock_db.load_positions.return_value = [
        {
            "market_id": "m1",
            "direction": "YES",
            "entry_price": 0.45,
            "amount": 50.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "peak_pnl_pct": 0.05,
            "tokens": '{"YES": "0xa", "NO": "0xb"}',
        }
    ]
    await manager.load_from_db()
    assert "m1" in manager._positions
    assert manager._positions["m1"].entry_price == 0.45
    assert manager._positions["m1"].tokens == {"YES": "0xa", "NO": "0xb"}
