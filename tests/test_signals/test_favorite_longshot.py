import pytest
from datetime import datetime, timezone, timedelta
from polymarket_bot.signals.favorite_longshot import FavoriteLongshotSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def flb():
    return FavoriteLongshotSignal()


def _make_market(price=0.50, days_out=15, volume=10000):
    return Market(
        id="m1", question="Will X happen?",
        end_date=datetime.now(timezone.utc) + timedelta(days=days_out),
        tokens={"YES": "a", "NO": "b"}, current_price=price,
        volume=volume,
    )


async def test_short_extreme_favorite(flb):
    market = _make_market(price=0.95, volume=10000, days_out=10)
    signal = await flb.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.NO
    assert signal.source == "favorite_longshot"
    assert 0.0 < signal.confidence <= 0.70


async def test_buy_extreme_longshot(flb):
    market = _make_market(price=0.05, volume=10000, days_out=10)
    signal = await flb.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.YES
    assert 0.0 < signal.confidence <= 0.40


async def test_no_signal_for_midrange(flb):
    market = _make_market(price=0.50, volume=10000)
    signal = await flb.evaluate(market)
    assert signal is None


async def test_no_signal_low_volume(flb):
    market = _make_market(price=0.95, volume=1000, days_out=10)
    signal = await flb.evaluate(market)
    assert signal is None


async def test_no_signal_too_few_days(flb):
    market = _make_market(price=0.95, volume=10000, days_out=1)
    signal = await flb.evaluate(market)
    assert signal is None


def test_can_evaluate_extreme_prices(flb):
    assert flb.can_evaluate(_make_market(price=0.95)) is True
    assert flb.can_evaluate(_make_market(price=0.05)) is True
    assert flb.can_evaluate(_make_market(price=0.50)) is False
