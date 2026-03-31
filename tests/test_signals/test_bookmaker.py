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


async def test_evaluate_no_signal_small_edge(bookmaker_signal, market):
    """Edge < 2% should not produce a signal."""
    odds_data = {"implied_probability": 0.41, "bookmakers_count": 5}
    with patch.object(bookmaker_signal, "_fetch_odds", new_callable=AsyncMock, return_value=odds_data):
        result = await bookmaker_signal.evaluate(market)
        assert result is None


def test_identify_yes_outcome_home_team():
    """Should match the team mentioned in the question."""
    result = BookmakerSignal._identify_yes_outcome(
        "Will Team A win?", "Team A", "Team B",
    )
    assert result == "Team A"


def test_identify_yes_outcome_away_team():
    result = BookmakerSignal._identify_yes_outcome(
        "Will Team B win the finals?", "Team A", "Team B",
    )
    assert result == "Team B"


def test_identify_yes_outcome_partial_match():
    """Should match partial team names (e.g., 'Lakers' in 'Los Angeles Lakers')."""
    result = BookmakerSignal._identify_yes_outcome(
        "Will the Lakers win tonight?", "Los Angeles Lakers", "Boston Celtics",
    )
    assert result == "Los Angeles Lakers"


def test_match_market_only_averages_yes_outcome():
    """The core bug fix: should only average the YES outcome's odds, not all outcomes."""
    sig = BookmakerSignal(api_key="test")
    events = [{
        "home_team": "Team A",
        "away_team": "Team B",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Team A", "price": -150},  # 60% implied
                        {"name": "Team B", "price": 130},   # 43% implied
                    ],
                }],
            },
            {
                "key": "fanduel",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Team A", "price": -160},  # 61.5% implied
                        {"name": "Team B", "price": 140},   # 41.7% implied
                    ],
                }],
            },
        ],
    }]

    market = Market(
        id="m1", question="Will Team A win the championship?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
        category="sports",
    )

    result = sig._match_market_to_event(market, events)
    assert result is not None
    # Should be ~60% (Team A's implied prob), NOT ~50% (average of all outcomes)
    assert result["implied_probability"] > 0.55
    assert result["implied_probability"] < 0.65
    assert result["bookmakers_count"] == 2


def test_match_market_old_bug_would_return_50():
    """Regression test: the old code averaged all outcomes → ~50% for any 2-outcome market."""
    sig = BookmakerSignal(api_key="test")
    events = [{
        "home_team": "Favorite",
        "away_team": "Underdog",
        "bookmakers": [{
            "key": "bookie1",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Favorite", "price": -300},  # 75% implied
                    {"name": "Underdog", "price": 250},   # 28.6% implied
                ],
            }],
        }],
    }]

    market = Market(
        id="m1", question="Will Favorite win?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.50,
        category="sports",
    )

    result = sig._match_market_to_event(market, events)
    assert result is not None
    # Must be ~75% (Favorite), NOT ~50% (average of 75% + 28.6%)
    assert result["implied_probability"] > 0.65


def test_american_to_probability():
    assert BookmakerSignal.american_to_probability(-150) == pytest.approx(0.6, abs=0.01)
    assert BookmakerSignal.american_to_probability(150) == pytest.approx(0.4, abs=0.01)
    assert BookmakerSignal.american_to_probability(-100) == pytest.approx(0.5, abs=0.01)


def test_decimal_to_probability():
    assert BookmakerSignal.decimal_to_probability(2.0) == pytest.approx(0.5, abs=0.01)
    assert BookmakerSignal.decimal_to_probability(1.5) == pytest.approx(0.667, abs=0.01)
