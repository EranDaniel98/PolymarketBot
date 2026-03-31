import pytest
import time
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from polymarket_bot.signals.whale import WhaleSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will X happen?",
        end_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xyes", "NO": "0xno"}, current_price=0.50,
    )


@pytest.fixture
def whale():
    return WhaleSignal(
        single_trade_threshold=10000,
        cumulative_threshold=25000,
        window_seconds=300,
    )


def _make_trade(size, price, side="BUY", maker="0xwhale", ts=None):
    return {
        "size": size, "price": price, "side": side,
        "maker": maker, "timestamp": ts or time.time(),
    }


async def test_detect_single_large_trade(whale, market):
    trades = [_make_trade(size=30000, price=0.50, side="BUY")]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        assert result.source == "whale"
        assert result.direction == Direction.YES
        assert result.confidence > 0


async def test_detect_cumulative_whale(whale, market):
    # Multiple trades from same address totalling > $25K (each $10K)
    trades = [
        _make_trade(size=20000, price=0.50, side="BUY", maker="0xbig"),
        _make_trade(size=20000, price=0.50, side="BUY", maker="0xbig"),
        _make_trade(size=20000, price=0.50, side="BUY", maker="0xbig"),
    ]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        assert result.direction == Direction.YES


async def test_no_signal_below_threshold(whale, market):
    trades = [_make_trade(size=100, price=0.50, side="BUY")]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is None


async def test_whale_direction(whale, market):
    # Whale sells → NO direction
    trades = [_make_trade(size=30000, price=0.50, side="SELL")]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        assert result.direction == Direction.NO


async def test_can_evaluate_requires_tokens(whale):
    market_no_tokens = Market(
        id="m2", question="No tokens?",
        end_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
        tokens={}, current_price=0.50,
    )
    assert whale.can_evaluate(market_no_tokens) is False


async def test_tracked_wallet_lower_threshold(market):
    whale = WhaleSignal(
        single_trade_threshold=10000,
        cumulative_threshold=25000,
        window_seconds=300,
        tracked_wallets=["0xtracked"],
    )
    # $6K trade from tracked wallet (below $10K but above $5K = half threshold)
    trades = [_make_trade(size=12000, price=0.50, side="BUY", maker="0xtracked")]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        assert result.direction == Direction.YES


async def test_no_double_counting_volume(whale, market):
    """Regression: a maker exceeding both single-trade AND cumulative thresholds
    should have their volume counted exactly once."""
    # Single trade of $15K (exceeds $10K single) from one maker
    # Same maker cumulative is also $15K (below $25K cumulative, so only single triggers)
    trades = [_make_trade(size=30000, price=0.50, side="BUY", maker="0xwhale")]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        # Volume should be $15K (30000 * 0.50), NOT $30K from double-counting
        assert "$15,000" in result.reasoning
        assert "$30,000" not in result.reasoning


async def test_no_double_counting_both_thresholds(market):
    """When a maker hits BOTH single and cumulative thresholds, count once."""
    whale = WhaleSignal(
        single_trade_threshold=10000,
        cumulative_threshold=20000,
        window_seconds=300,
    )
    # 3 trades: first is $15K (single threshold), total is $25K (cumulative threshold)
    trades = [
        _make_trade(size=30000, price=0.50, side="BUY", maker="0xbig"),  # $15K
        _make_trade(size=10000, price=0.50, side="BUY", maker="0xbig"),  # $5K
        _make_trade(size=10000, price=0.50, side="BUY", maker="0xbig"),  # $5K
    ]
    with patch.object(whale, "_fetch_trades", new_callable=AsyncMock, return_value=trades):
        result = await whale.evaluate(market)
        assert result is not None
        # Total should be $25K exactly (all from same maker, counted once)
        assert "$25,000" in result.reasoning
