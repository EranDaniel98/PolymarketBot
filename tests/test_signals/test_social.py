import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.social import SocialSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Ethereum flip Bitcoin?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.20,
    )


@pytest.fixture
def social_signal():
    return SocialSignal(subreddits=["polymarket", "crypto"])


async def test_social_signal_name(social_signal):
    assert social_signal.name == "social"


async def test_evaluate_no_posts(social_signal, market):
    with patch.object(social_signal, "_fetch_reddit_posts", new_callable=AsyncMock, return_value=[]):
        result = await social_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_posts(social_signal, market):
    posts = [
        {"title": "ETH bullish rally continues!", "score": 500, "num_comments": 200},
    ] * 25  # 25 posts for full volume factor
    with patch.object(social_signal, "_fetch_reddit_posts", new_callable=AsyncMock, return_value=posts):
        result = await social_signal.evaluate(market)
        assert result is not None
        assert result.source == "social"
        assert 0.0 <= result.confidence <= 1.0
