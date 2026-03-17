import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.bookmaker import BookmakerSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Team A win the championship?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
        category="sports",
    )


@pytest.fixture
def bookmaker_signal():
    return BookmakerSignal(api_key="test-key", poll_interval=60)


async def test_bookmaker_signal_name(bookmaker_signal):
    assert bookmaker_signal.name == "bookmaker"


async def test_evaluate_no_odds(bookmaker_signal, market):
    with patch.object(bookmaker_signal, "_fetch_odds", new_callable=AsyncMock, return_value=None):
        result = await bookmaker_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_odds(bookmaker_signal, market):
    odds_data = {"implied_probability": 0.55, "bookmakers_count": 5}
    with patch.object(bookmaker_signal, "_fetch_odds", new_callable=AsyncMock, return_value=odds_data):
        result = await bookmaker_signal.evaluate(market)
        assert result is not None
        assert result.source == "bookmaker"
        assert result.direction == Direction.YES
