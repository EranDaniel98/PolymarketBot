import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from polymarket_bot.signals.divergence import DivergenceSignal
from polymarket_bot.data_sources.metaculus import MetaculusForecast
from polymarket_bot.data_sources.manifold import ManifoldMarket
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will X happen by end of year?",
        end_date=datetime.now(timezone.utc) + timedelta(days=30),
        tokens={"YES": "a", "NO": "b"}, current_price=0.40,
        volume=10000,
    )


@pytest.fixture
def divergence():
    sig = DivergenceSignal(min_divergence=0.08, min_forecasters=50, min_days=3)
    sig._metaculus = AsyncMock()
    sig._manifold = AsyncMock()
    return sig


async def test_fires_on_10pct_divergence(divergence, market):
    """10% divergence with 100 forecasters should produce a signal."""
    divergence._metaculus.search.return_value = [
        MetaculusForecast(
            question="Will X happen by end of year?",
            community_prediction=0.55,  # 15% above Polymarket's 0.40
            forecaster_count=100,
            url="https://metaculus.com/q/1",
        ),
    ]
    divergence._manifold.search.return_value = []

    signal = await divergence.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.YES
    assert signal.confidence > 0


async def test_no_signal_small_divergence(divergence, market):
    """3% divergence should not produce a signal."""
    divergence._metaculus.search.return_value = [
        MetaculusForecast(
            question="Will X happen by end of year?",
            community_prediction=0.43,  # Only 3% above
            forecaster_count=100,
            url="",
        ),
    ]
    divergence._manifold.search.return_value = []

    signal = await divergence.evaluate(market)
    assert signal is None


async def test_no_signal_few_forecasters(divergence, market):
    divergence._metaculus.search.return_value = [
        MetaculusForecast(
            question="Will X happen by end of year?",
            community_prediction=0.60,
            forecaster_count=10,  # Too few
            url="",
        ),
    ]
    divergence._manifold.search.return_value = []

    signal = await divergence.evaluate(market)
    assert signal is None


async def test_boost_with_two_platforms(divergence, market):
    """Both Metaculus and Manifold disagreeing should boost confidence."""
    divergence._metaculus.search.return_value = [
        MetaculusForecast(
            question="Will X happen by end of year?",
            community_prediction=0.55,
            forecaster_count=100,
            url="",
        ),
    ]
    divergence._manifold.search.return_value = [
        ManifoldMarket(
            question="Will X happen by end of year?",
            probability=0.52,
            url="",
        ),
    ]

    signal = await divergence.evaluate(market)
    assert signal is not None
    # With two platforms, confidence should be boosted
    assert signal.confidence > 0


async def test_no_signal_too_few_days(divergence, market):
    market.end_date = datetime.now(timezone.utc) + timedelta(days=1)
    divergence._metaculus.search.return_value = [
        MetaculusForecast(
            question="Will X happen by end of year?",
            community_prediction=0.70,
            forecaster_count=100,
            url="",
        ),
    ]
    signal = await divergence.evaluate(market)
    assert signal is None
