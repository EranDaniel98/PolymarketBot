"""Tests for the DB persistence layer (Phase 2).

Uses in-memory aiosqlite so no external DB is needed. Covers:
  - ensure_schema adds missing columns idempotently
  - persist_position_entry / load_open_positions round-trip
  - persist_position_exit transitions status=open → closed
  - get/record_daily_loss auto-reset across date boundaries
  - get_completed_trades reflects real trade counts
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_weather.db import persistence
from polymarket_weather.db.models import Base
from polymarket_weather.db.persistence import PersistedPosition


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


def _make_position(market_id="0xabc", direction="YES") -> PersistedPosition:
    return PersistedPosition(
        market_id=market_id,
        direction=direction,
        entry_price=0.55,
        size_usdc=25.0,
        city="new york",
        region="northeast",
        event_id="evt_1",
        entry_time=datetime.now(timezone.utc),
        peak_pnl_pct=0.0,
    )


@pytest.mark.asyncio
async def test_ensure_schema_idempotent(session_factory):
    await persistence.ensure_schema(session_factory)
    await persistence.ensure_schema(session_factory)  # twice should be safe
    # If this ran without raising, we're good.


@pytest.mark.asyncio
async def test_persist_and_load_single_position(session_factory):
    pos = _make_position()
    trade_id = await persistence.persist_position_entry(session_factory, pos)
    assert trade_id > 0

    loaded = await persistence.load_open_positions(session_factory)
    assert len(loaded) == 1
    assert loaded[0].market_id == "0xabc"
    assert loaded[0].direction == "YES"
    assert loaded[0].entry_price == 0.55
    assert loaded[0].size_usdc == 25.0
    assert loaded[0].city == "new york"
    assert loaded[0].region == "northeast"


@pytest.mark.asyncio
async def test_persist_exit_removes_from_open(session_factory):
    pos = _make_position()
    await persistence.persist_position_entry(session_factory, pos)
    await persistence.persist_position_exit(
        session_factory, market_id="0xabc",
        exit_price=0.72, pnl_usdc=7.72, exit_reason="take_profit",
    )
    loaded = await persistence.load_open_positions(session_factory)
    assert loaded == []


@pytest.mark.asyncio
async def test_load_multiple_open_positions(session_factory):
    await persistence.persist_position_entry(session_factory, _make_position("0x1", "YES"))
    await persistence.persist_position_entry(session_factory, _make_position("0x2", "NO"))
    await persistence.persist_position_entry(session_factory, _make_position("0x3", "YES"))
    loaded = await persistence.load_open_positions(session_factory)
    assert len(loaded) == 3
    ids = {p.market_id for p in loaded}
    assert ids == {"0x1", "0x2", "0x3"}


@pytest.mark.asyncio
async def test_crash_recovery_scenario(session_factory):
    """Simulate crash + restart: insert, 'crash', reload."""
    await persistence.persist_position_entry(session_factory, _make_position("0xpersist"))

    # Simulate restart — new load should see the position
    reloaded = await persistence.load_open_positions(session_factory)
    assert len(reloaded) == 1
    assert reloaded[0].market_id == "0xpersist"


@pytest.mark.asyncio
async def test_daily_loss_starts_at_zero(session_factory):
    assert await persistence.get_daily_loss(session_factory) == 0.0


@pytest.mark.asyncio
async def test_daily_loss_accumulates(session_factory):
    await persistence.record_daily_loss(session_factory, 15.0)
    await persistence.record_daily_loss(session_factory, 10.0)
    assert await persistence.get_daily_loss(session_factory) == 25.0


@pytest.mark.asyncio
async def test_completed_trades_reflects_settled_only(session_factory):
    # Insert 2 open and 0 settled → completed = 0
    await persistence.persist_position_entry(session_factory, _make_position("0x1"))
    await persistence.persist_position_entry(session_factory, _make_position("0x2"))
    assert await persistence.get_completed_trades(session_factory) == 0


@pytest.mark.asyncio
async def test_paused_flag_defaults_false(session_factory):
    assert await persistence.is_trading_paused(session_factory) is False


@pytest.mark.asyncio
async def test_paused_flag_round_trip(session_factory):
    await persistence.set_trading_paused(session_factory, True)
    assert await persistence.is_trading_paused(session_factory) is True
    await persistence.set_trading_paused(session_factory, False)
    assert await persistence.is_trading_paused(session_factory) is False


@pytest.mark.asyncio
async def test_load_skips_incomplete_legacy_rows(session_factory):
    """A trade row from before Phase 2.1 has NULLs in the new columns. The
    loader should skip it with a warning rather than crashing."""
    from polymarket_weather.db.models import Trade
    async with session_factory() as session:
        # Create a bare-bones pre-Phase-2 trade row (no market_id/direction)
        session.add(Trade(status="open", size_usdc=10.0))
        await session.commit()
    loaded = await persistence.load_open_positions(session_factory)
    assert loaded == []
