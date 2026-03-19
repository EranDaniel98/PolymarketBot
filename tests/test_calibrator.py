import pytest
from unittest.mock import AsyncMock
from polymarket_bot.calibrator import WeightCalibrator, DEFAULT_WEIGHTS


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_trade_count.return_value = 0
    db.get_accuracy_report.return_value = {}
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
    mock_db.get_accuracy_report.return_value = {
        "llm": {"accuracy": 0.70, "n_signals": 20, "avg_confidence": 0.80},
        "favorite_longshot": {"accuracy": 0.60, "n_signals": 15, "avg_confidence": 0.50},
        "polls": {"accuracy": 0.55, "n_signals": 10, "avg_confidence": 0.60},
    }

    result = await calibrator.maybe_recalibrate()
    assert result is True
    weights = calibrator.weights
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    # LLM should have highest weight (best accuracy * confidence)
    assert weights["llm"] > weights["polls"]
    # All sources should have some weight
    for w in weights.values():
        assert w > 0


async def test_no_recalibration_without_enough_new_trades(calibrator, mock_db):
    mock_db.get_trade_count.return_value = 25
    mock_db.get_accuracy_report.return_value = {
        "llm": {"accuracy": 0.70, "n_signals": 20, "avg_confidence": 0.80},
    }
    # First calibration succeeds
    await calibrator.maybe_recalibrate()
    # Second attempt with only 2 more trades should not recalibrate
    mock_db.get_trade_count.return_value = 27
    result = await calibrator.maybe_recalibrate()
    assert result is False
