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


def test_binance_price_cached_in_monitor(monitor):
    """Crypto exchange prices should be stored and retrievable from cache."""
    monitor._update_price("binance", "BTC/USDT", 105000.0)
    monitor._update_price("binance", "ETH/USDT", 3500.0)
    assert monitor.get_cached_price("binance", "BTC/USDT") == 105000.0
    assert monitor.get_cached_price("binance", "ETH/USDT") == 3500.0


async def test_crypto_price_update_event_published(monitor):
    """The crypto_price_update event should be publishable via the bus."""
    received = []
    monitor._bus.subscribe("crypto_price_update", lambda e: received.append(e))
    await monitor._bus.publish("crypto_price_update", {
        "symbol": "BTC/USDT", "price": 105000.0,
    })
    assert len(received) == 1
    assert received[0]["symbol"] == "BTC/USDT"
    assert received[0]["price"] == 105000.0
