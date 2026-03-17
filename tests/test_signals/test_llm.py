import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.signals.llm import LLMSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will AI pass the Turing test by 2027?",
        end_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.35,
    )


@pytest.fixture
def llm_signal():
    return LLMSignal(api_key="test-key", model="claude-sonnet-4-6-20250514")


async def test_llm_signal_name(llm_signal):
    assert llm_signal.name == "llm"


async def test_evaluate_parses_response(llm_signal, market):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"probability": 0.65, "reasoning": "Strong AI progress"}')]

    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await llm_signal.evaluate(market)
        assert result is not None
        assert result.source == "llm"
        assert result.direction == Direction.YES
        assert result.confidence == 0.6


async def test_evaluate_handles_api_error(llm_signal, market):
    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await llm_signal.evaluate(market)
        assert result is None
