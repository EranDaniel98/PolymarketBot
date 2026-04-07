import pytest
from polymarket_weather.trading.executor import TradeExecutor


@pytest.fixture
def executor():
    return TradeExecutor(paper_trading=True, paper_balance=1000.0,
                         max_slippage=0.02, max_retries=3)


async def test_paper_trade_buy(executor):
    result = await executor.execute_order("tok_yes", "BUY", 25.0, 0.55, "limit")
    assert result.status == "filled"
    assert result.order_id.startswith("paper_")
    assert abs(result.fill_price - 0.55) < 0.01


async def test_paper_trade_sell(executor):
    result = await executor.execute_order("tok_yes", "SELL", 25.0, 0.70, "limit")
    assert result.status == "filled"


async def test_paper_balance_tracking(executor):
    assert executor.get_balance() == 1000.0
    await executor.execute_order("tok", "BUY", 100.0, 0.50, "limit")
    assert executor.get_balance() < 1000.0


def test_slippage_ok(executor):
    assert executor.check_slippage(0.55, 0.56) is True


def test_slippage_too_high(executor):
    assert executor.check_slippage(0.55, 0.60) is False


def test_slippage_zero_price(executor):
    assert executor.check_slippage(0.0, 0.50) is False


async def test_paper_start(executor):
    await executor.start()  # Should not raise


async def test_paper_stop(executor):
    await executor.stop()  # Should not raise
