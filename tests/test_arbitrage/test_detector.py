import pytest
from polymarket_bot.arbitrage.detector import OpportunityDetector


@pytest.fixture
def detector():
    return OpportunityDetector(min_spread=0.05)


def test_detect_opportunity(detector):
    prices = {"polymarket": 0.40, "kalshi": 0.50}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1", "kalshi": "k1"},
    )
    assert result is not None
    assert result.spread == pytest.approx(0.10, abs=0.01)


def test_no_opportunity_small_spread(detector):
    prices = {"polymarket": 0.50, "kalshi": 0.52}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1", "kalshi": "k1"},
    )
    assert result is None


def test_detect_bookmaker_divergence(detector):
    prices = {"polymarket": 0.40, "bookmaker": 0.52}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1"},
    )
    assert result is not None
