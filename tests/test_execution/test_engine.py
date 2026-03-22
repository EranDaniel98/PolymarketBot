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
        tokens={"YES": "0xa", "NO": "0xb"},
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
        tokens={"YES": "0xa", "NO": "0xb"},
    )
    with patch.object(engine, "_get_best_price", new_callable=AsyncMock, return_value=0.55):
        await engine.execute(decision, current_price=0.50)
        mock_bus.publish.assert_not_called()


async def test_paper_trading_mode(mock_db, mock_bus):
    config = ExecutionConfig(paper_trading=True)
    engine = ExecutionEngine(config=config, database=mock_db, event_bus=mock_bus)
    tokens = {"YES": "0xa", "NO": "0xb"}

    order_id, price, status = await engine._place_order(
        tokens, Direction.YES, 12.0, 0.50, OrderType.LIMIT,
    )
    assert order_id.startswith("paper_")
    assert status == OrderStatus.FILLED
    assert abs(price - 0.50) < 0.01


async def test_paper_trading_exit(mock_db, mock_bus):
    config = ExecutionConfig(paper_trading=True)
    engine = ExecutionEngine(config=config, database=mock_db, event_bus=mock_bus)
    tokens = {"YES": "0xa", "NO": "0xb"}

    order_id, price, status = await engine._place_order(
        tokens, Direction.YES, 12.0, 0.50, OrderType.LIMIT, is_exit=True,
    )
    assert order_id.startswith("paper_")
    assert status == OrderStatus.FILLED


async def test_execute_with_is_exit(engine, mock_bus, mock_db):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.99, signals=[], order_type=OrderType.LIMIT,
        tokens={"YES": "0xa", "NO": "0xb"}, is_exit=True,
    )
    with patch.object(engine, "_place_order", new_callable=AsyncMock,
                     return_value=("ord456", 0.55, OrderStatus.FILLED)) as mock_place:
        await engine.execute(decision, current_price=0.55)
        # Verify is_exit was passed through
        call_kwargs = mock_place.call_args
        assert call_kwargs[0][5] is True or call_kwargs[1].get("is_exit") is True


async def test_execute_exit_propagates_is_exit_to_execution(engine, mock_bus, mock_db):
    """TradeExecution published for exit trades must carry is_exit=True."""
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.99, signals=[], order_type=OrderType.LIMIT,
        tokens={"YES": "0xa", "NO": "0xb"}, is_exit=True,
    )
    with patch.object(engine, "_place_order", new_callable=AsyncMock,
                     return_value=("ord789", 0.55, OrderStatus.FILLED)):
        await engine.execute(decision, current_price=0.55)
        published = mock_bus.publish.call_args[0][1]
        assert published.is_exit is True


async def test_execute_entry_has_is_exit_false(engine, mock_bus, mock_db):
    """TradeExecution published for normal entries must have is_exit=False."""
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
        tokens={"YES": "0xa", "NO": "0xb"},
    )
    with patch.object(engine, "_place_order", new_callable=AsyncMock,
                     return_value=("ord123", 0.50, OrderStatus.FILLED)):
        await engine.execute(decision, current_price=0.50)
        published = mock_bus.publish.call_args[0][1]
        assert published.is_exit is False


async def test_order_book_depth_paper_mode(mock_db, mock_bus):
    """Paper mode should skip depth check and return original amount."""
    config = ExecutionConfig(paper_trading=True)
    engine = ExecutionEngine(config=config, database=mock_db, event_bus=mock_bus)
    has_liq, size = await engine.check_order_book_depth("0xtoken", "BUY", 100.0)
    assert has_liq is True
    assert size == 100.0


async def test_order_book_depth_reduces_size(mock_db, mock_bus):
    """When order is >50% of book liquidity, size should be reduced."""
    config = ExecutionConfig(paper_trading=False)
    engine = ExecutionEngine(config=config, database=mock_db, event_bus=mock_bus)

    mock_client = MagicMock()
    book_data = {
        "asks": [
            {"price": "0.50", "size": "100"},  # $50
            {"price": "0.51", "size": "100"},  # $51
        ],
    }
    mock_client.get_order_book.return_value = book_data
    engine._clob_client = mock_client

    async def fake_to_thread(fn, *args):
        return fn(*args)

    with patch("polymarket_bot.execution.engine.asyncio.to_thread", side_effect=fake_to_thread):
        has_liq, size = await engine.check_order_book_depth("0xtoken", "BUY", 80.0)
        assert has_liq is True
        # Total liq = 0.50*100 + 0.51*100 = 101. 50% = 50.5. 80 > 50.5, so reduced
        assert size == pytest.approx(50.5, abs=0.1)
