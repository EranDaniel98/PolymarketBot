import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.signals.llm import (
    LLMSignal, _ModelBackend, _parse_llm_response,
    _aggregate_trimmed_mean, _aggregate_weighted,
)
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
    mock_client = MagicMock()
    llm_signal._client = mock_client

    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.8, "reasoning": "Strong AI progress"}', {"input_tokens": 500, "output_tokens": 100}))
    backend.provider = "anthropic"
    backend.model = "claude-test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
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
    llm_signal._client = MagicMock()
    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(side_effect=Exception("API error"))
    backend.provider = "anthropic"
    backend.model = "test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await llm_signal.evaluate(market)
                    assert result is None


async def test_evaluate_low_confidence_returns_none(llm_signal, market):
    """When LLM reports low self-confidence, signal should be filtered out."""
    llm_signal._client = MagicMock()
    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(return_value=('{"probability": 0.40, "confidence": 0.1, "reasoning": "Uncertain"}', {"input_tokens": 500, "output_tokens": 50}))
    backend.provider = "anthropic"
    backend.model = "test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await llm_signal.evaluate(market)
                    # edge = 0.05, edge_conf = 0.1, final = 0.1 * 0.1 = 0.01 < 0.1
                    assert result is None


async def test_screening_filters_uninteresting_markets(llm_signal, market):
    """When Haiku screening returns NO, Opus should not be called."""
    with patch.object(llm_signal, "_client", create=True) as mock_client:
        with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=False):
            result = await llm_signal.evaluate(market)
            assert result is None
            # Opus (full model) should never have been called
            mock_client.messages.create.assert_not_called()


async def test_screening_failure_skips_market(llm_signal, market):
    """When screening raises an exception, market should be SKIPPED (not analyzed)."""
    llm_signal._client = MagicMock()
    llm_signal._client.messages.create = AsyncMock(side_effect=Exception("API down"))
    result = await llm_signal._quick_screen(market)
    assert result is False


async def test_screening_passes_interesting_markets(llm_signal, market):
    """When Haiku says YES, full analysis with Opus runs."""
    llm_signal._client = MagicMock()

    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.7, "reasoning": "Analysis"}', {"input_tokens": 500, "output_tokens": 100}))
    backend.provider = "anthropic"
    backend.model = "claude-test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    screening_resp = MagicMock()
    screening_resp.content = [MagicMock(text="YES This market seems mispriced.")]
    llm_signal._client.messages.create = AsyncMock(return_value=screening_resp)

    with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
        with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                result = await llm_signal.evaluate(market)
                assert result is not None


# ---- Ensemble tests ----


async def test_ensemble_queries_multiple_backends(market):
    """With ensemble enabled, all backends should be queried."""
    signal = LLMSignal(
        api_key="test", ensemble_enabled=True,
        ensemble_models=[
            {"provider": "anthropic", "model": "claude-test", "weight": 0.5},
            {"provider": "anthropic", "model": "claude-test2", "weight": 0.5},
        ],
    )

    backend1 = MagicMock(spec=_ModelBackend)
    backend1.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.7, "reasoning": "A"}', {"input_tokens": 400, "output_tokens": 80}))
    backend1.provider = "anthropic"
    backend1.model = "claude-test"
    backend1.weight = 0.5

    backend2 = MagicMock(spec=_ModelBackend)
    backend2.query = AsyncMock(return_value=('{"probability": 0.70, "confidence": 0.8, "reasoning": "B"}', {"input_tokens": 400, "output_tokens": 80}))
    backend2.provider = "anthropic"
    backend2.model = "claude-test2"
    backend2.weight = 0.5

    signal._backends = [backend1, backend2]
    signal._client = MagicMock()

    with patch.object(signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await signal.evaluate(market)
                    assert result is not None
                    backend1.query.assert_called_once()
                    backend2.query.assert_called_once()
                    assert "ensemble(2)" in result.reasoning


def test_ensemble_trimmed_mean():
    """3 results: drop highest and lowest, average the middle."""
    results = [
        {"probability": 0.50, "confidence": 0.6, "reasoning": "low"},
        {"probability": 0.65, "confidence": 0.7, "reasoning": "mid"},
        {"probability": 0.80, "confidence": 0.8, "reasoning": "high"},
    ]
    agg = _aggregate_trimmed_mean(results)
    assert agg["probability"] == pytest.approx(0.65)
    assert agg["confidence"] == pytest.approx(0.7)


async def test_ensemble_graceful_degradation(market):
    """If one backend fails, signal should still come from the remaining."""
    signal = LLMSignal(api_key="test", ensemble_enabled=True)

    backend_ok = MagicMock(spec=_ModelBackend)
    backend_ok.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.7, "reasoning": "OK"}', {"input_tokens": 500, "output_tokens": 100}))
    backend_ok.provider = "anthropic"
    backend_ok.model = "ok-model"
    backend_ok.weight = 0.5

    backend_fail = MagicMock(spec=_ModelBackend)
    backend_fail.query = AsyncMock(side_effect=Exception("timeout"))
    backend_fail.provider = "openai"
    backend_fail.model = "fail-model"
    backend_fail.weight = 0.5

    signal._backends = [backend_ok, backend_fail]
    signal._client = MagicMock()

    with patch.object(signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await signal.evaluate(market)
                    assert result is not None
                    assert result.direction == Direction.YES


async def test_ensemble_all_fail_returns_none(market):
    """If all backends fail, evaluate should return None."""
    signal = LLMSignal(api_key="test", ensemble_enabled=True)

    backend1 = MagicMock(spec=_ModelBackend)
    backend1.query = AsyncMock(side_effect=Exception("err1"))
    backend1.provider = "anthropic"
    backend1.model = "m1"
    backend1.weight = 0.5

    backend2 = MagicMock(spec=_ModelBackend)
    backend2.query = AsyncMock(side_effect=Exception("err2"))
    backend2.provider = "openai"
    backend2.model = "m2"
    backend2.weight = 0.5

    signal._backends = [backend1, backend2]
    signal._client = MagicMock()

    with patch.object(signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await signal.evaluate(market)
                    assert result is None


async def test_legacy_single_model_unchanged(market):
    """With ensemble_enabled=False, behavior matches original single-model."""
    signal = LLMSignal(api_key="test", ensemble_enabled=False)

    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.8, "reasoning": "Solo"}', {"input_tokens": 500, "output_tokens": 100}))
    backend.provider = "anthropic"
    backend.model = "claude-test"
    backend.weight = 1.0

    signal._backends = [backend]
    signal._client = MagicMock()

    with patch.object(signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    result = await signal.evaluate(market)
                    assert result is not None
                    assert "ensemble" not in result.reasoning


# ---- Parse/aggregation unit tests ----


def test_parse_llm_response_valid():
    text = '{"probability": 0.65, "confidence": 0.8, "reasoning": "test"}'
    result = _parse_llm_response(text)
    assert result is not None
    assert result["probability"] == 0.65


def test_parse_llm_response_markdown():
    text = '```json\n{"probability": 0.65, "confidence": 0.8, "reasoning": "test"}\n```'
    result = _parse_llm_response(text)
    assert result is not None


def test_parse_llm_response_invalid():
    assert _parse_llm_response("not json at all") is None


def test_aggregate_weighted():
    results = [
        {"probability": 0.60, "confidence": 0.7, "reasoning": "A"},
        {"probability": 0.80, "confidence": 0.9, "reasoning": "B"},
    ]
    agg = _aggregate_weighted(results, [0.4, 0.6])
    expected_prob = (0.60 * 0.4 + 0.80 * 0.6) / 1.0
    assert agg["probability"] == pytest.approx(expected_prob)


async def test_circuit_breaker_trips_on_all_fail(llm_signal, market):
    """After all backends fail, circuit should open and block next evaluate."""
    llm_signal._client = MagicMock()
    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(side_effect=Exception("quota exceeded"))
    backend.provider = "anthropic"
    backend.model = "test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    with patch.object(llm_signal, "_gather_polymarket_context", new_callable=AsyncMock, return_value=""):
                        with patch.object(llm_signal, "_gather_related_markets", new_callable=AsyncMock, return_value=""):
                            with patch.object(llm_signal, "_gather_metaculus", new_callable=AsyncMock, return_value=""):
                                with patch.object(llm_signal, "_gather_wikipedia", new_callable=AsyncMock, return_value=""):
                                    # First call trips the circuit
                                    result = await llm_signal.evaluate(market)
                                    assert result is None
                                    assert llm_signal._consecutive_failures == 1
                                    assert llm_signal._circuit_open_until is not None

                                    # Second call is blocked by circuit — backend not called
                                    backend.query.reset_mock()
                                    result = await llm_signal.evaluate(market)
                                    assert result is None
                                    backend.query.assert_not_called()


async def test_circuit_breaker_resets_on_success(llm_signal, market):
    """A successful evaluation should reset the circuit breaker."""
    llm_signal._client = MagicMock()
    llm_signal._consecutive_failures = 3
    llm_signal._circuit_open_until = None  # Expired

    backend = MagicMock(spec=_ModelBackend)
    backend.query = AsyncMock(return_value=('{"probability": 0.65, "confidence": 0.7, "reasoning": "OK"}', {"input_tokens": 500, "output_tokens": 100}))
    backend.provider = "anthropic"
    backend.model = "test"
    backend.weight = 1.0
    llm_signal._backends = [backend]

    with patch.object(llm_signal, "_quick_screen", new_callable=AsyncMock, return_value=True):
        with patch.object(llm_signal, "_gather_news", new_callable=AsyncMock, return_value=""):
            with patch.object(llm_signal, "_gather_reddit", new_callable=AsyncMock, return_value=""):
                with patch.object(llm_signal, "_gather_odds", new_callable=AsyncMock, return_value=""):
                    with patch.object(llm_signal, "_gather_polymarket_context", new_callable=AsyncMock, return_value=""):
                        with patch.object(llm_signal, "_gather_related_markets", new_callable=AsyncMock, return_value=""):
                            with patch.object(llm_signal, "_gather_metaculus", new_callable=AsyncMock, return_value=""):
                                with patch.object(llm_signal, "_gather_wikipedia", new_callable=AsyncMock, return_value=""):
                                    result = await llm_signal.evaluate(market)
                                    # Should produce a signal (edge = 0.65 - 0.35 = 0.30)
                                    assert result is not None
                                    assert llm_signal._consecutive_failures == 0
                                    assert llm_signal._circuit_open_until is None
