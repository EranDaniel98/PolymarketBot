import pytest
from unittest.mock import AsyncMock
from polymarket_bot.arbitrage.monitor import PriceMonitor
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.event_bus import EventBus


@pytest.fixture
def monitor():
    mapper = MarketMapper({"poly_m1": {"kalshi": "k1"}})
    detector = OpportunityDetector(min_spread=0.05)
    bus = EventBus()
    db = AsyncMock()
    return PriceMonitor(mapper=mapper, detector=detector, event_bus=bus, database=db)


def test_get_cached_price_none(monitor):
    assert monitor.get_cached_price("polymarket", "unknown") is None


def test_update_and_get_cached_price(monitor):
    monitor._update_price("polymarket", "poly_m1", 0.55)
    assert monitor.get_cached_price("polymarket", "poly_m1") == 0.55


def test_update_multiple_platforms(monitor):
    monitor._update_price("polymarket", "poly_m1", 0.45)
    monitor._update_price("kalshi", "poly_m1", 0.55)
    assert monitor.get_cached_price("polymarket", "poly_m1") == 0.45
    assert monitor.get_cached_price("kalshi", "poly_m1") == 0.55
