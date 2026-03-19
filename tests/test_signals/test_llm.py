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
    mock_response.content = [MagicMock(
        text='{"probability": 0.65, "confidence": 0.8, "reasoning": "Strong AI progress"}'
    )]

    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        # Mock context gathering to avoid real HTTP calls
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value="- AI headlines"):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value="- Reddit posts"):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await llm_signal.evaluate(market)
                    assert result is not None
                    assert result.source == "llm"
                    assert result.direction == Direction.YES
                    # edge = 0.65 - 0.35 = 0.30, edge_conf = min(0.6, 0.95) = 0.6
                    # final = 0.6 * 0.8 (llm confidence) = 0.48
                    assert 0.3 < result.confidence < 0.7


async def test_evaluate_handles_api_error(llm_signal, market):
    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await llm_signal.evaluate(market)
                    assert result is None


async def test_evaluate_low_confidence_returns_none(llm_signal, market):
    """When LLM reports low self-confidence, signal should be filtered out."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(
        text='{"probability": 0.40, "confidence": 0.1, "reasoning": "Uncertain"}'
    )]

    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await llm_signal.evaluate(market)
                    # edge = 0.05, edge_conf = 0.1, final = 0.1 * 0.1 = 0.01 < 0.1
                    assert result is None
