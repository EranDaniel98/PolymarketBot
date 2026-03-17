import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.signals.news import NewsSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Bitcoin reach $100k by end of 2026?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.45,
    )


@pytest.fixture
def news_signal():
    return NewsSignal(api_key="test-key", poll_interval=300)


async def test_news_signal_name(news_signal):
    assert news_signal.name == "news"


async def test_evaluate_returns_none_when_no_articles(news_signal, market):
    with patch.object(news_signal, "_fetch_articles", new_callable=AsyncMock, return_value=[]):
        result = await news_signal.evaluate(market)
        assert result is None


async def test_evaluate_returns_signal_with_articles(news_signal, market):
    articles = [
        {"title": "Bitcoin surges past $90k", "description": "Major rally continues"},
    ]
    with patch.object(news_signal, "_fetch_articles", new_callable=AsyncMock, return_value=articles):
        with patch.object(news_signal, "_analyze_sentiment", new_callable=AsyncMock,
                         return_value=(Direction.YES, 0.75, "Bullish news")):
            result = await news_signal.evaluate(market)
            assert result is not None
            assert result.source == "news"
            assert result.confidence == 0.75
            assert result.direction == Direction.YES
