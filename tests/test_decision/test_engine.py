import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from polymarket_bot.decision.engine import DecisionEngine
from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.models import Signal, Direction, Market, SignalEvent, OrderType


@pytest.fixture
def market():
    return Market(
        id="m1", question="Test?", end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
    )


@pytest.fixture
def mock_risk():
    risk = AsyncMock()
    risk.check.return_value = (True, "Approved")
    risk.calculate_position_size.return_value = 100.0
    risk.circuit_breaker_active = False
    return risk


@pytest.fixture
def mock_bus():
    bus = AsyncMock()
    return bus


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_signals.return_value = []
    return db


@pytest.fixture
def engine(mock_risk, mock_bus, mock_db):
    thresholds = ConfidenceThresholds(auto_execute=0.8, notify=0.5)
    signals_config = SignalsConfig()
    return DecisionEngine(
        risk_manager=mock_risk, event_bus=mock_bus, database=mock_db,
        thresholds=thresholds, signals_config=signals_config,
    )


def test_aggregate_signals_weighted(engine):
    signals = [
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.7, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite = engine.aggregate_signals(signals)
    assert 0.0 < composite < 1.0


def test_aggregate_signals_empty(engine):
    composite = engine.aggregate_signals([])
    assert composite == 0.0


def test_aggregate_conflicting_signals(engine):
    signals = [
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="llm", market_id="m1", direction=Direction.NO,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite = engine.aggregate_signals(signals)
    assert 0.4 < composite < 0.6


async def test_determine_action_high_confidence(engine):
    action = engine.determine_action(0.85)
    assert action == "auto_execute"


async def test_determine_action_medium_confidence(engine):
    action = engine.determine_action(0.65)
    assert action == "notify"


async def test_determine_action_low_confidence(engine):
    action = engine.determine_action(0.3)
    assert action == "log_only"
