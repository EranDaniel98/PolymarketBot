import pytest
from unittest.mock import AsyncMock
from polymarket_bot.calibrator import WeightCalibrator, DEFAULT_WEIGHTS


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_trade_count.return_value = 0
    db._fetch_all = AsyncMock(return_value=[])
    return db


@pytest.fixture
def calibrator(mock_db):
    return WeightCalibrator(database=mock_db, min_samples=5, recalibrate_every=5)


def test_initial_weights_are_defaults(calibrator):
    assert calibrator.weights == DEFAULT_WEIGHTS


async def test_no_recalibration_below_min_samples(calibrator, mock_db):
    mock_db.get_trade_count.return_value = 3
    result = await calibrator.maybe_recalibrate()
    assert result is False


async def test_recalibration_updates_weights(calibrator, mock_db):
    mock_db.get_trade_count.return_value = 25

    # Simulate signals with varying accuracy
    mock_db._fetch_all = AsyncMock(side_effect=[
        # First call: signals
        [
            {"source": "news", "direction": "YES", "market_id": "m1", "confidence": 0.8},
            {"source": "news", "direction": "YES", "market_id": "m2", "confidence": 0.7},
            {"source": "news", "direction": "YES", "market_id": "m3", "confidence": 0.9},
            {"source": "news", "direction": "YES", "market_id": "m4", "confidence": 0.6},
            {"source": "news", "direction": "YES", "market_id": "m5", "confidence": 0.8},
            {"source": "llm", "direction": "YES", "market_id": "m1", "confidence": 0.9},
            {"source": "llm", "direction": "YES", "market_id": "m2", "confidence": 0.85},
            {"source": "llm", "direction": "YES", "market_id": "m3", "confidence": 0.7},
            {"source": "llm", "direction": "YES", "market_id": "m4", "confidence": 0.8},
            {"source": "llm", "direction": "YES", "market_id": "m5", "confidence": 0.9},
        ],
        # Second call: trades (3 of 5 profitable for both sources)
        [
            {"market_id": "m1", "direction": "YES", "realized_pnl": 10.0},
            {"market_id": "m2", "direction": "YES", "realized_pnl": -5.0},
            {"market_id": "m3", "direction": "YES", "realized_pnl": 8.0},
            {"market_id": "m4", "direction": "YES", "realized_pnl": -3.0},
            {"market_id": "m5", "direction": "YES", "realized_pnl": 12.0},
        ],
    ])

    result = await calibrator.maybe_recalibrate()
    assert result is True
    weights = calibrator.weights
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    # All sources should have some weight
    for w in weights.values():
        assert w > 0
