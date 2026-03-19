import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from polymarket_bot.arbitrage.structural_arb import StructuralArbDetector, StructuralArbOpportunity
from polymarket_bot.models import Market, Direction


@pytest.fixture
def detector():
    return StructuralArbDetector(fee_rate=0.02, min_profit_pct=0.005)


def _market(yes_price, no_price):
    return Market(
        id="m1", question="Test?",
        end_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xyes", "NO": "0xno"},
        current_price=yes_price, no_price=no_price,
    )


def test_detects_profitable_opportunity(detector):
    # YES=0.48 + NO=0.48 = 0.96, profit = 1.0 - 0.96 - 0.02 = 0.02 (2%)
    market = _market(0.48, 0.48)
    opp = detector.check(market)
    assert opp is not None
    assert opp.expected_profit_pct == pytest.approx(0.02, abs=0.001)
    assert opp.yes_price == 0.48
    assert opp.no_price == 0.48


def test_no_opportunity_sum_too_high(detector):
    # YES=0.50 + NO=0.51 = 1.01 > 1.0
    market = _market(0.50, 0.51)
    opp = detector.check(market)
    assert opp is None


def test_no_opportunity_below_fee_threshold(detector):
    # YES=0.49 + NO=0.50 = 0.99, profit = 1.0 - 0.99 - 0.02 = -0.01 < min
    market = _market(0.49, 0.50)
    opp = detector.check(market)
    assert opp is None


def test_no_opportunity_missing_no_price(detector):
    market = _market(0.50, 0.0)
    opp = detector.check(market)
    assert opp is None


async def test_paired_execution_paper_mode():
    """Verify structural arb produces two trade executions in paper mode."""
    from polymarket_bot.execution.engine import ExecutionEngine
    from polymarket_bot.config import ExecutionConfig

    config = ExecutionConfig(paper_trading=True)
    db = AsyncMock()
    bus = AsyncMock()
    engine = ExecutionEngine(config=config, database=db, event_bus=bus)

    opp = StructuralArbOpportunity(
        market_id="m1", yes_price=0.48, no_price=0.48,
        combined_price=0.96, expected_profit_pct=0.02,
        tokens={"YES": "0xyes", "NO": "0xno"},
    )

    await engine.execute_structural_arb(opp, amount_per_side=50.0)

    # Should have saved 2 trades and published 2 executions
    assert db.save_trade.call_count == 2
    assert bus.publish.call_count == 2

    # Verify both directions
    directions = [call.args[1].direction for call in bus.publish.call_args_list]
    assert Direction.YES in directions
    assert Direction.NO in directions
