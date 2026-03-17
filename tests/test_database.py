import pytest
from datetime import datetime, timezone
from polymarket_bot.database import Database
from polymarket_bot.models import (
    Signal, Direction, TradeExecution, OrderStatus, Market,
)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_initialize_creates_tables(db):
    tables = await db.get_tables()
    assert "trades" in tables
    assert "signals" in tables
    assert "markets" in tables
    assert "portfolio" in tables
    assert "prices" in tables
    assert "orders" in tables


async def test_save_and_get_signal(db):
    signal = Signal(
        source="news", market_id="m1", direction=Direction.YES,
        confidence=0.75, reasoning="test", timestamp=datetime.now(timezone.utc),
    )
    await db.save_signal(signal)
    signals = await db.get_signals("m1")
    assert len(signals) == 1
    assert signals[0]["source"] == "news"
    assert signals[0]["confidence"] == 0.75


async def test_save_and_get_trade(db):
    trade = TradeExecution(
        market_id="m1", direction=Direction.YES, amount=100.0,
        price=0.55, order_id="ord1", status=OrderStatus.FILLED,
    )
    await db.save_trade(trade)
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["market_id"] == "m1"


async def test_get_daily_pnl_empty(db):
    pnl = await db.get_daily_pnl()
    assert pnl == 0.0


async def test_get_total_exposure_empty(db):
    exposure = await db.get_total_exposure()
    assert exposure == 0.0


async def test_get_trade_count(db):
    count = await db.get_trade_count()
    assert count == 0
