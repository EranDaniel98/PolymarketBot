import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from polymarket_bot.thin_market_detector import ThinMarketDetector
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Market, Direction, Signal


def _make_market(volume=5000, days_out=15):
    return Market(
        id="m1", question="Will X happen?",
        end_date=datetime.now(timezone.utc) + timedelta(days=days_out),
        tokens={"YES": "a", "NO": "b"}, current_price=0.5,
        volume=volume,
    )


def test_is_thin_market_low_volume():
    bus = EventBus()
    detector = ThinMarketDetector(event_bus=bus, max_volume=10000)
    market = _make_market(volume=5000)
    assert detector.is_thin_market(market) is True


def test_is_not_thin_market_high_volume():
    bus = EventBus()
    detector = ThinMarketDetector(event_bus=bus, max_volume=10000)
    market = _make_market(volume=50000)
    assert detector.is_thin_market(market) is False


def test_is_not_thin_market_zero_volume():
    bus = EventBus()
    detector = ThinMarketDetector(event_bus=bus, max_volume=10000)
    market = _make_market(volume=0)
    assert detector.is_thin_market(market) is False


async def test_fast_track_publishes_boosted_signal():
    bus = EventBus()
    received = []

    async def capture(event):
        received.append(event)

    bus.subscribe("signal", capture)

    mock_llm = AsyncMock()
    mock_llm.evaluate = AsyncMock(return_value=Signal(
        source="llm", market_id="m1", direction=Direction.YES,
        confidence=0.60, reasoning="Test analysis",
        timestamp=datetime.now(timezone.utc),
    ))

    detector = ThinMarketDetector(
        event_bus=bus, llm_plugin=mock_llm, confidence_boost=1.15,
    )

    market = _make_market(volume=5000)
    await detector._fast_track_analysis(market)

    assert len(received) == 1
    # Confidence should be boosted by 15%
    assert received[0].signal.confidence == pytest.approx(0.69, abs=0.01)
    assert "[THIN MARKET boost]" in received[0].signal.reasoning


async def test_fast_track_no_signal_no_publish():
    bus = EventBus()
    received = []

    async def capture(event):
        received.append(event)

    bus.subscribe("signal", capture)

    mock_llm = AsyncMock()
    mock_llm.evaluate = AsyncMock(return_value=None)

    detector = ThinMarketDetector(event_bus=bus, llm_plugin=mock_llm)
    market = _make_market(volume=5000)
    await detector._fast_track_analysis(market)

    assert len(received) == 0
