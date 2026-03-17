import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.polls import PollSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will candidate X win the 2026 election?",
        end_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.45,
        category="politics",
    )


@pytest.fixture
def poll_signal():
    return PollSignal(poll_interval=3600)


async def test_poll_signal_name(poll_signal):
    assert poll_signal.name == "polls"


async def test_evaluate_no_data(poll_signal, market):
    with patch.object(poll_signal, "_fetch_poll_data", new_callable=AsyncMock, return_value=None):
        result = await poll_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_data(poll_signal, market):
    poll_data = {"implied_probability": 0.55, "source": "RCP Average"}
    with patch.object(poll_signal, "_fetch_poll_data", new_callable=AsyncMock, return_value=poll_data):
        result = await poll_signal.evaluate(market)
        assert result is not None
        assert result.source == "polls"
        assert result.direction == Direction.YES
