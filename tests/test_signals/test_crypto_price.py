import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from polymarket_bot.signals.crypto_price import (
    CryptoPriceSignal,
    parse_market_question,
    parse_strike_price,
    sigmoid,
    time_adjusted_steepness,
)
from polymarket_bot.models import Market, Direction


def _make_market(
    price=0.50, days_out=3, question="Will Bitcoin be above $100,000 on April 1?",
):
    return Market(
        id="m1",
        question=question,
        end_date=datetime.now(timezone.utc) + timedelta(days=days_out),
        tokens={"YES": "a", "NO": "b"},
        current_price=price,
        category="crypto",
    )


# --- parse_market_question tests ---


def test_parse_market_question_btc_above():
    result = parse_market_question("Will Bitcoin be above $100,000 on April 1?")
    assert result is not None
    symbol, strike, is_above = result
    assert symbol == "BTC/USDT"
    assert strike == 100_000.0
    assert is_above is True


def test_parse_market_question_eth_below():
    result = parse_market_question("Will ETH be below $3,000 on March 31?")
    assert result is not None
    symbol, strike, is_above = result
    assert symbol == "ETH/USDT"
    assert strike == 3_000.0
    assert is_above is False


def test_parse_market_question_k_suffix():
    result = parse_market_question("BTC above $100k?")
    assert result is not None
    _, strike, _ = result
    assert strike == 100_000.0


def test_parse_market_question_sol():
    result = parse_market_question("Will Solana exceed $200 by April 5?")
    assert result is not None
    symbol, strike, is_above = result
    assert symbol == "SOL/USDT"
    assert strike == 200.0
    assert is_above is True


def test_parse_market_question_non_crypto_returns_none():
    assert parse_market_question("Will it rain in NYC tomorrow?") is None
    assert parse_market_question("Who will win the 2028 election?") is None
    assert parse_market_question("") is None


# --- parse_strike_price tests ---


def test_parse_strike_price_commas():
    assert parse_strike_price("100,000") == 100_000.0
    assert parse_strike_price("1,234,567.89") == 1_234_567.89


def test_parse_strike_price_k():
    assert parse_strike_price("100k") == 100_000.0
    assert parse_strike_price("50K") == 50_000.0


def test_parse_strike_price_plain():
    assert parse_strike_price("3000") == 3_000.0
    assert parse_strike_price("0.50") == 0.50


def test_parse_strike_price_invalid():
    assert parse_strike_price("abc") is None
    assert parse_strike_price("") is None


# --- sigmoid tests ---


def test_sigmoid_center():
    """x=0 should give ~0.5."""
    assert sigmoid(0) == pytest.approx(0.5)


def test_sigmoid_positive():
    """Large positive x → close to 1.0."""
    assert sigmoid(0.5) > 0.99


def test_sigmoid_negative():
    """Large negative x → close to 0.0."""
    assert sigmoid(-0.5) < 0.01


def test_sigmoid_symmetry():
    """sigmoid(x) + sigmoid(-x) ≈ 1.0."""
    assert sigmoid(0.1) + sigmoid(-0.1) == pytest.approx(1.0)


def test_time_adjusted_steepness_daily():
    """1-day market should use base steepness."""
    assert time_adjusted_steepness(1.0) == pytest.approx(20.0)


def test_time_adjusted_steepness_weekly():
    """7-day market should be much flatter (less confident)."""
    s = time_adjusted_steepness(7.0)
    assert 7 < s < 8  # ~7.56


def test_time_adjusted_steepness_near_expiry():
    """6h market should be much steeper (more confident)."""
    s = time_adjusted_steepness(0.25)  # 6 hours
    assert s > 35


# --- can_evaluate tests ---


def test_can_evaluate_crypto_market():
    signal = CryptoPriceSignal()
    market = _make_market(question="Will Bitcoin be above $100,000 on April 1?")
    assert signal.can_evaluate(market) is True


def test_can_evaluate_non_crypto_market():
    signal = CryptoPriceSignal()
    market = _make_market(question="Will it rain in NYC tomorrow?")
    assert signal.can_evaluate(market) is False


# --- evaluate tests ---


@pytest.fixture
def crypto_signal():
    """CryptoPriceSignal with mocked exchange."""
    signal = CryptoPriceSignal(min_divergence=0.05, max_days_to_expiry=7)
    # Pre-populate price cache to avoid needing real exchange
    signal._price_cache = {
        "BTC/USDT": 105_000.0,
        "ETH/USDT": 3_500.0,
        "SOL/USDT": 180.0,
    }
    return signal


async def test_evaluate_spot_above_strike_bullish(crypto_signal):
    """When spot >> strike and market underprices YES, should signal YES."""
    # BTC at $105k, market asks "above $100k?" at 50% price — clearly underpriced
    market = _make_market(price=0.50, days_out=1, question="Will Bitcoin be above $100,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.YES
    assert signal.source == "crypto_price"
    assert signal.confidence > 0.0


async def test_evaluate_spot_below_strike_bearish(crypto_signal):
    """When spot << strike and market overprices YES, should signal NO."""
    # BTC at $105k, market asks "above $120k?" at 80% price — overpriced
    crypto_signal._price_cache["BTC/USDT"] = 105_000.0
    market = _make_market(price=0.80, days_out=1, question="Will Bitcoin be above $120,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.NO
    assert signal.confidence > 0.0


async def test_evaluate_no_signal_small_divergence(crypto_signal):
    """When spot is near strike, divergence is small — no signal."""
    # BTC at $105k, market asks "above $105k?" at ~50% — spot ≈ strike, sigmoid ~50%
    market = _make_market(price=0.50, days_out=3, question="Will Bitcoin be above $105,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    # divergence should be small since spot ≈ strike
    assert signal is None


async def test_evaluate_no_signal_too_far_expiry(crypto_signal):
    """Markets too far from expiry should be skipped."""
    market = _make_market(price=0.50, days_out=30, question="Will Bitcoin be above $100,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    assert signal is None


async def test_evaluate_expired_market_skipped(crypto_signal):
    """Expired markets should be skipped."""
    market = _make_market(price=0.50, days_out=-1, question="Will Bitcoin be above $100,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    assert signal is None


async def test_steepness_varies_with_time_to_expiry(crypto_signal):
    """Near-expiry markets should produce stronger signals (steeper sigmoid → more divergence)."""
    market_far = _make_market(price=0.40, days_out=5, question="Will Bitcoin be above $100,000 on April 1?")
    market_near = _make_market(price=0.40, days_out=0.5, question="Will Bitcoin be above $100,000 on April 1?")

    signal_far = await crypto_signal.evaluate(market_far)
    signal_near = await crypto_signal.evaluate(market_near)

    assert signal_far is not None
    assert signal_near is not None
    # Near-expiry uses steeper sigmoid → higher implied prob → larger divergence → higher confidence
    assert signal_near.confidence > signal_far.confidence


async def test_evaluate_below_market(crypto_signal):
    """'Below' market should work correctly."""
    # BTC at $105k, market asks "below $100k?" at 60% — overpriced (spot is above $100k)
    market = _make_market(price=0.60, days_out=1, question="Will BTC be below $100,000 on April 1?")
    signal = await crypto_signal.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.NO  # Spot above strike → "below" should be NO


async def test_evaluate_no_price_returns_none():
    """When no price is available, should return None."""
    signal = CryptoPriceSignal()
    signal._price_cache = {}  # Empty cache
    signal._exchanges = []  # No exchanges
    market = _make_market(price=0.50, days_out=1, question="Will Bitcoin be above $100,000?")
    result = await signal.evaluate(market)
    assert result is None
