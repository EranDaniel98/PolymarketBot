import pytest
from datetime import datetime, timezone
from polymarket_bot.models import (
    Direction, Signal, Market, TradeDecision, TradeExecution,
    ArbitrageOpportunity, OrderType, OrderStatus, SignalEvent,
)


def test_signal_confidence_must_be_between_0_and_1():
    with pytest.raises(ValueError):
        Signal(source="test", market_id="m1", direction=Direction.YES,
               confidence=1.5, reasoning="test", timestamp=datetime.now(timezone.utc))

    with pytest.raises(ValueError):
        Signal(source="test", market_id="m1", direction=Direction.YES,
               confidence=-0.1, reasoning="test", timestamp=datetime.now(timezone.utc))


def test_signal_valid():
    s = Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.75, reasoning="Strong signal", timestamp=datetime.now(timezone.utc))
    assert s.source == "news"
    assert s.confidence == 0.75


def test_market_model():
    m = Market(id="0x123", question="Will X happen?", end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
               tokens={"YES": "0xabc", "NO": "0xdef"}, current_price=0.55)
    assert m.id == "0x123"
    assert m.current_price == 0.55


def test_trade_decision():
    td = TradeDecision(market_id="m1", direction=Direction.YES, amount=50.0,
                       confidence=0.85, signals=[], order_type=OrderType.LIMIT)
    assert td.amount == 50.0
    assert td.order_type == OrderType.LIMIT


def test_arbitrage_opportunity():
    arb = ArbitrageOpportunity(
        market_ids={"polymarket": "m1", "kalshi": "k1"},
        platforms=["polymarket", "kalshi"],
        prices={"polymarket": 0.45, "kalshi": 0.55},
        spread=0.10,
        estimated_profit=5.0,
        confidence=0.9,
        time_sensitivity="high",
    )
    assert arb.spread == 0.10
