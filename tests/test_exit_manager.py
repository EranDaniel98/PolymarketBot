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


async def test_max_hold_time_losing_position(bus, mock_db):
    """Losing positions held beyond max_hold_hours should trigger exit."""
    rules = ExitRule(max_hold_hours=24, edge_gone_threshold=0.02, stop_loss=-0.15)
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    # Create a position held for 48 hours, currently at a small loss
    # Price difference enough to avoid edge_gone (> 0.03 with time decay)
    # but still negative PnL
    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.50, amount=100.0,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=48),
    )
    mgr._positions["m1"] = pos
    # Price at 0.46 -> -8% loss (above stop_loss of -15%), edge_remaining=0.04 > threshold
    mgr.set_price_getter(lambda platform, mid: 0.46)

    reason = await mgr._check_exit(pos)
    assert reason is not None
    assert "Max hold time" in reason


async def test_max_hold_time_winning_position_no_exit(bus, mock_db):
    """Winning positions should NOT be force-exited even if held beyond max_hold_hours."""
    rules = ExitRule(max_hold_hours=24, take_profit=0.50)
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.50, amount=100.0,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=48),
    )
    mgr._positions["m1"] = pos
    # Price at 0.55 -> +10% gain (below take_profit of 50%)
    mgr.set_price_getter(lambda platform, mid: 0.55)

    reason = await mgr._check_exit(pos)
    # Should not trigger max hold time because position is in profit
    assert reason is None or "Max hold" not in (reason or "")


async def test_correlated_exposure(bus, mock_db):
    """get_correlated_exposure should sum positions by category."""
    rules = ExitRule()
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    await mgr.track_entry("m1", Direction.YES, 0.50, 100.0, category="politics")
    await mgr.track_entry("m2", Direction.NO, 0.40, 200.0, category="politics")
    await mgr.track_entry("m3", Direction.YES, 0.60, 150.0, category="crypto")

    assert mgr.get_correlated_exposure("politics") == 300.0
    assert mgr.get_correlated_exposure("crypto") == 150.0
    assert mgr.get_correlated_exposure("sports") == 0.0


async def test_load_from_db_restores_end_date_and_category(bus, mock_db):
    """load_from_db must restore end_date and category from the database."""
    mock_db.load_positions.return_value = [
        {
            "market_id": "m1",
            "direction": "YES",
            "entry_price": 0.45,
            "amount": 50.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "peak_pnl_pct": 0.05,
            "tokens": '{"YES": "0xa"}',
            "end_date": "2026-12-31T00:00:00+00:00",
            "category": "politics",
        }
    ]
    rules = ExitRule()
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)
    await mgr.load_from_db()
    pos = mgr._positions["m1"]
    assert pos.end_date is not None
    assert pos.end_date.year == 2026
    assert pos.category == "politics"


async def test_trigger_exit_marks_pending(bus, mock_db):
    """_trigger_exit must mark market as pending so monitor skips it."""
    rules = ExitRule()
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc),
    )
    mgr._positions["m1"] = pos
    await mgr._trigger_exit(pos, "Test exit")
    # Position stays in _positions (not removed until confirmed fill)
    assert "m1" in mgr._positions
    # But marked as pending exit so monitor loop skips it
    assert "m1" in mgr._pending_exits


async def test_track_exit_clears_pending(bus, mock_db):
    """track_exit must clear the pending flag and remove position."""
    rules = ExitRule()
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    pos = TrackedPosition(
        market_id="m1", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc),
    )
    mgr._positions["m1"] = pos
    mgr._pending_exits.add("m1")
    await mgr.track_exit("m1")
    assert "m1" not in mgr._positions
    assert "m1" not in mgr._pending_exits


async def test_trailing_stop_activation_threshold(bus, mock_db):
    """Trailing stop should respect configurable activation threshold."""
    rules = ExitRule(trailing_stop=0.08, trailing_stop_activation=0.03)
    mgr = ExitManager(event_bus=bus, database=mock_db, rules=rules)

    await mgr.track_entry("m1", Direction.YES, 0.40, 100.0)
    pos = mgr._positions["m1"]
    # Peak was only 4%, which is above 3% activation
    pos.peak_pnl_pct = 0.04

    # Current pnl at -5% (below trailing trigger of 0.04 - 0.08 = -0.04)
    mgr.set_price_getter(lambda platform, mid: 0.38)
    reason = await mgr._check_exit(pos)
    assert reason is not None
    assert "Trailing stop" in reason
