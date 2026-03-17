import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from polymarket_bot.execution.engine import ExecutionEngine
from polymarket_bot.config import ExecutionConfig
from polymarket_bot.models import TradeDecision, Direction, OrderType, OrderStatus


@pytest.fixture
def exec_config():
    return ExecutionConfig(default_order_type="limit", max_slippage=0.01, max_retries=3)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_bus():
    return AsyncMock()


@pytest.fixture
def engine(exec_config, mock_db, mock_bus):
    return ExecutionEngine(config=exec_config, database=mock_db, event_bus=mock_bus)


def test_check_slippage_ok(engine):
    assert engine.check_slippage(target_price=0.50, actual_price=0.504) is True


def test_check_slippage_too_high(engine):
    assert engine.check_slippage(target_price=0.50, actual_price=0.52) is False


async def test_execute_trade_success(engine, mock_bus, mock_db):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(engine, "_place_order", new_callable=AsyncMock,
                     return_value=("ord123", 0.50, OrderStatus.FILLED)):
        await engine.execute(decision, current_price=0.50)
        mock_bus.publish.assert_called_once()
        mock_db.save_trade.assert_called_once()


async def test_execute_trade_slippage_reject(engine, mock_bus):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.MARKET,
    )
    with patch.object(engine, "_get_best_price", new_callable=AsyncMock, return_value=0.55):
        await engine.execute(decision, current_price=0.50)
        mock_bus.publish.assert_not_called()
