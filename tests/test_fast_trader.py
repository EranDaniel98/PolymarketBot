import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from polymarket_bot.fast_trader import FastTrader
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Market


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Biden win the 2026 midterms?",
        end_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
        tokens={"YES": "a", "NO": "b"}, current_price=0.55,
        category="politics",
    )


def test_relevance_scoring(bus, market):
    ft = FastTrader(event_bus=bus, markets=[market])
    # Headline closely matches market question
    score = ft._compute_relevance("Biden wins midterm election in landslide", market)
    assert score > 0.3

    # Unrelated headline
    score = ft._compute_relevance("New restaurant opens in downtown NYC", market)
    assert score < 0.2


def test_direction_inference(bus, market):
    ft = FastTrader(event_bus=bus)
    assert ft._infer_direction("Biden wins the election", market).value == "YES"
    assert ft._infer_direction("Biden loses key state", market).value == "NO"


async def test_breaking_news_emits_signal(bus, market):
    received = []

    async def capture(event):
        received.append(event)

    bus.subscribe("signal", capture)

    ft = FastTrader(event_bus=bus, markets=[market])
    ft._seen_headlines = set()

    # Mock headlines with a resolution keyword matching the market
    with patch.object(ft, "_fetch_latest_headlines", new_callable=AsyncMock, return_value=[
        {"title": "Biden officially wins 2026 midterm elections", "source": "news", "score": 100},
    ]):
        await ft._check_breaking_news()

    assert len(received) >= 1
    assert received[0].signal.source == "fast_news"


async def test_deduplicates_headlines(bus, market):
    received = []
    bus.subscribe("signal", lambda e: received.append(e))

    ft = FastTrader(event_bus=bus, markets=[market])

    headlines = [
        {"title": "Biden officially wins 2026 midterms", "source": "news", "score": 100},
    ]

    with patch.object(ft, "_fetch_latest_headlines", new_callable=AsyncMock, return_value=headlines):
        await ft._check_breaking_news()
        count_first = len(received)
        await ft._check_breaking_news()  # Same headline again
        assert len(received) == count_first  # No duplicates
