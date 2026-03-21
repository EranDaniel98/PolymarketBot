import pytest
from datetime import datetime, timezone
from polymarket_bot.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_get_confidence_calibration(db):
    now = datetime.now(timezone.utc)
    for i in range(5):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.60, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 3 else "No")

    for i in range(5, 10):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.85, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 9 else "No")

    buckets = await db.get_confidence_calibration("llm", bucket_size=0.20)
    assert len(buckets) >= 2
    low_bucket = next(b for b in buckets if b["bucket_min"] <= 0.60 < b["bucket_max"])
    assert low_bucket["accuracy"] == pytest.approx(0.6, abs=0.01)
    high_bucket = next(b for b in buckets if b["bucket_min"] <= 0.85 < b["bucket_max"])
    assert high_bucket["accuracy"] == pytest.approx(0.8, abs=0.01)


async def test_get_confidence_gap(db):
    now = datetime.now(timezone.utc)
    for i in range(10):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.80, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 6 else "No")

    gap = await db.get_confidence_gap("llm")
    assert gap is not None
    assert gap["avg_confidence"] == pytest.approx(0.80, abs=0.01)
    assert gap["actual_accuracy"] == pytest.approx(0.60, abs=0.01)
    assert gap["gap"] == pytest.approx(0.20, abs=0.01)


async def test_get_fee_impact_report(db):
    from polymarket_bot.models import TradeExecution, Direction, OrderStatus
    for i in range(5):
        trade = TradeExecution(
            market_id=f"m{i}", direction=Direction.YES, amount=25.0,
            price=0.50, order_id=f"ord{i}", status=OrderStatus.FILLED,
            fees=0.50, realized_pnl=2.0 if i < 3 else -1.0,
        )
        await db.save_trade(trade)

    report = await db.get_fee_impact_report()
    assert report["total_fees"] == pytest.approx(2.50, abs=0.01)
    assert report["total_pnl"] == pytest.approx(4.0, abs=0.01)
    assert report["fee_pct_of_volume"] > 0
