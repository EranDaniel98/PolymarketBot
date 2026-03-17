import pytest
from unittest.mock import AsyncMock
from polymarket_bot.decision.risk import RiskManager, half_kelly
from polymarket_bot.config import RiskConfig
from polymarket_bot.models import Direction, Signal, TradeDecision, OrderType
from datetime import datetime, timezone


@pytest.fixture
def risk_config():
    return RiskConfig(
        max_position_pct=0.05, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        max_correlated_exposure_pct=0.15, min_edge=0.03, kelly_fraction=0.5,
        bootstrap_trades=50, bootstrap_size_pct=0.01, cooldown_seconds=300,
    )


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_total_exposure.return_value = 0.0
    db.get_daily_pnl.return_value = 0.0
    db.get_trade_count.return_value = 0
    return db


@pytest.fixture
def risk_manager(risk_config, mock_db):
    return RiskManager(config=risk_config, database=mock_db, bankroll=5000.0)


async def test_calculate_position_size_bootstrap(risk_manager):
    size = await risk_manager.calculate_position_size(confidence=0.9, market_price=0.50)
    assert size == 50.0


async def test_calculate_position_size_kelly(risk_manager, mock_db):
    mock_db.get_trade_count.return_value = 51
    size = await risk_manager.calculate_position_size(confidence=0.8, market_price=0.40)
    assert size <= 250.0


async def test_check_risk_passes(risk_manager):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is True


async def test_check_risk_rejects_low_edge(risk_manager):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.52, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is False
    assert "edge" in reason.lower()


async def test_check_risk_rejects_circuit_breaker(risk_manager, mock_db):
    mock_db.get_daily_pnl.return_value = -600.0
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "circuit breaker" in reason.lower()


async def test_check_risk_rejects_max_exposure(risk_manager, mock_db):
    mock_db.get_total_exposure.return_value = 2600.0
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "exposure" in reason.lower()


async def test_half_kelly_formula():
    result = half_kelly(p=0.7, market_price=1/3, fraction=0.5)
    assert abs(result - 0.275) < 0.01
