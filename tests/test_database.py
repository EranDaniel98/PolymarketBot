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


# --- Portfolio persistence tests ---

async def test_save_and_load_position(db):
    await db.save_position("m1", "YES", 50.0, 0.45, tokens='{"YES": "0xa"}')
    positions = await db.load_positions()
    assert len(positions) == 1
    assert positions[0]["market_id"] == "m1"
    assert positions[0]["direction"] == "YES"
    assert positions[0]["amount"] == 50.0
    assert positions[0]["entry_price"] == 0.45
    assert positions[0]["tokens"] == '{"YES": "0xa"}'


async def test_delete_position(db):
    await db.save_position("m1", "YES", 50.0, 0.45)
    await db.delete_position("m1")
    positions = await db.load_positions()
    assert len(positions) == 0


async def test_update_position_peak(db):
    await db.save_position("m1", "YES", 50.0, 0.45)
    await db.update_position_peak("m1", 0.12)
    positions = await db.load_positions()
    assert positions[0]["peak_pnl_pct"] == 0.12


async def test_save_position_upsert(db):
    await db.save_position("m1", "YES", 50.0, 0.45)
    await db.save_position("m1", "YES", 75.0, 0.50)  # Should replace
    positions = await db.load_positions()
    assert len(positions) == 1
    assert positions[0]["amount"] == 75.0


# --- Daily report query tests ---

async def test_get_total_pnl_empty(db):
    pnl = await db.get_total_pnl()
    assert pnl == 0.0


async def test_get_win_rate_empty(db):
    rate = await db.get_win_rate()
    assert rate == 0.0


async def test_get_daily_trades_empty(db):
    trades = await db.get_daily_trades()
    assert len(trades) == 0


# --- Signal Outcomes (Accuracy Tracking) ---

async def test_save_signal_outcome(db):
    await db.save_signal_outcome(
        "llm", "m1", "YES", 0.85, 0.45, datetime.now(timezone.utc),
    )
    rows = await db._fetch_all("SELECT * FROM signal_outcomes")
    assert len(rows) == 1
    assert rows[0]["source"] == "llm"
    assert rows[0]["predicted_direction"] == "YES"
    assert rows[0]["was_correct"] is None  # Not yet resolved


async def test_record_resolution_backfills(db):
    now = datetime.now(timezone.utc)
    await db.save_signal_outcome("llm", "m1", "YES", 0.85, 0.45, now)
    await db.save_signal_outcome("polls", "m1", "NO", 0.60, 0.45, now)

    await db.record_resolution("m1", "Yes")

    rows = await db._fetch_all("SELECT * FROM signal_outcomes ORDER BY source")
    assert len(rows) == 2
    # "llm" predicted YES, outcome is Yes → correct
    llm_row = next(r for r in rows if r["source"] == "llm")
    assert llm_row["was_correct"] == 1
    assert llm_row["actual_outcome"] == "Yes"
    # "polls" predicted NO, outcome is Yes → incorrect
    polls_row = next(r for r in rows if r["source"] == "polls")
    assert polls_row["was_correct"] == 0


async def test_get_signal_accuracy(db):
    now = datetime.now(timezone.utc)
    # Create 10 signals, 7 correct
    for i in range(10):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.80, 0.45, now)
        outcome = "Yes" if i < 7 else "No"
        await db.record_resolution(f"m{i}", outcome)

    result = await db.get_signal_accuracy("llm", min_signals=5)
    assert result is not None
    assert result["accuracy"] == 0.7
    assert result["n_signals"] == 10


async def test_get_signal_accuracy_insufficient(db):
    now = datetime.now(timezone.utc)
    await db.save_signal_outcome("llm", "m1", "YES", 0.80, 0.45, now)
    await db.record_resolution("m1", "Yes")

    result = await db.get_signal_accuracy("llm", min_signals=10)
    assert result is None


async def test_get_accuracy_report(db):
    now = datetime.now(timezone.utc)
    for i in range(5):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.80, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 4 else "No")

    report = await db.get_accuracy_report()
    assert "llm" in report
    assert report["llm"]["accuracy"] == 0.8
    assert report["llm"]["n_signals"] == 5


async def test_get_unresolved_market_ids(db):
    now = datetime.now(timezone.utc)
    await db.save_signal_outcome("llm", "m1", "YES", 0.80, 0.45, now)
    await db.save_signal_outcome("llm", "m2", "NO", 0.60, 0.55, now)
    await db.record_resolution("m1", "Yes")

    unresolved = await db.get_unresolved_market_ids()
    assert "m2" in unresolved
    assert "m1" not in unresolved
