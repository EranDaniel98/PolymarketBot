"""
Tests for polymarket_weather.markets.parser
"""

from polymarket_weather.markets.parser import (
    parse_market_question,
    detect_city,
)


# ---------------------------------------------------------------------------
# Range markets (Polymarket's most common format)
# ---------------------------------------------------------------------------


def test_range_between_and_fahrenheit():
    result = parse_market_question(
        "Will the high temp in NYC be between 50 and 54 degrees Fahrenheit on April 15?"
    )
    assert result is not None
    assert result.threshold == 50.0
    assert result.threshold_upper == 54.0
    assert result.unit == "F"
    assert result.direction == "range"


def test_range_to_fahrenheit():
    result = parse_market_question(
        "Will the high temperature in Chicago be 60 to 64 degrees Fahrenheit?"
    )
    assert result is not None
    assert result.threshold == 60.0
    assert result.threshold_upper == 64.0
    assert result.direction == "range"


def test_range_dash():
    result = parse_market_question("NYC high temp 40-44°F on April 5?")
    assert result is not None
    assert result.threshold == 40.0
    assert result.threshold_upper == 44.0


def test_range_celsius():
    result = parse_market_question(
        "Will London high temperature be between 4 and 5 degrees Celsius on April 10?"
    )
    assert result is not None
    assert result.unit == "C"
    assert result.threshold == 4.0
    assert result.threshold_upper == 5.0


# ---------------------------------------------------------------------------
# Or above / or below (Polymarket actual format)
# ---------------------------------------------------------------------------


def test_or_above():
    result = parse_market_question(
        "Will the high temp in NYC be 55 degrees Fahrenheit or above on April 15?"
    )
    assert result is not None
    assert result.threshold == 55.0
    assert result.direction == "above"


def test_or_below():
    result = parse_market_question(
        "Will the high temp in NYC be 34 degrees Fahrenheit or below on April 15?"
    )
    assert result is not None
    assert result.threshold == 34.0
    assert result.direction == "below"


# ---------------------------------------------------------------------------
# Exceed / above / below
# ---------------------------------------------------------------------------


def test_exceed():
    result = parse_market_question(
        "Will the high temperature in Chicago exceed 80 degrees Fahrenheit on July 4?"
    )
    assert result is not None
    assert result.threshold == 80.0
    assert result.direction == "above"


def test_above():
    result = parse_market_question("Temperature above 90 degrees F in Phoenix?")
    assert result is not None
    assert result.threshold == 90.0
    assert result.direction == "above"


def test_below():
    result = parse_market_question(
        "Will temperature drop below 20 degrees Fahrenheit in Denver?"
    )
    assert result is not None
    assert result.threshold == 20.0
    assert result.direction == "below"


# ---------------------------------------------------------------------------
# Named thresholds
# ---------------------------------------------------------------------------


def test_below_freezing():
    result = parse_market_question(
        "Will NYC see temperatures below freezing on January 15?"
    )
    assert result is not None
    assert result.threshold == 32.0  # freezing in F
    assert result.direction == "below"


def test_above_freezing():
    result = parse_market_question(
        "Will temperatures stay above freezing in Chicago?"
    )
    assert result is not None
    assert result.threshold == 32.0
    assert result.direction == "above"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_negative_temperature():
    result = parse_market_question(
        "Will the low temperature in Denver drop to -5 degrees Fahrenheit?"
    )
    assert result is not None
    assert result.threshold == -5.0


def test_decimal_threshold():
    result = parse_market_question("Temperature above 32.5 degrees Fahrenheit?")
    assert result is not None
    assert result.threshold == 32.5


def test_unparseable():
    result = parse_market_question("Who will win the 2026 Super Bowl?")
    assert result is None


def test_default_unit_is_fahrenheit():
    result = parse_market_question("Temperature above 80 degrees in Phoenix?")
    assert result is not None
    assert result.unit == "F"  # Default


# ---------------------------------------------------------------------------
# City detection
# ---------------------------------------------------------------------------


def test_detect_city_nyc():
    assert detect_city("highest temperature in new york city on april 5") == "new york city"


def test_detect_city_alias():
    assert detect_city("highest temperature in nyc on april 5") == "nyc"


def test_detect_city_la():
    assert detect_city("Will the high temp in los angeles be above 80?") == "los angeles"


def test_detect_city_longer_match_first():
    """'new york city' should match before 'new york'."""
    assert detect_city("temperature in new york city") == "new york city"


def test_detect_city_unknown():
    assert detect_city("temperature in atlantis") is None


# ---------------------------------------------------------------------------
# Metric detection
# ---------------------------------------------------------------------------


def test_metric_is_temperature():
    result = parse_market_question(
        "Will the high temperature in NYC be above 80 degrees F?"
    )
    assert result is not None
    assert result.metric == "temperature"


# ---------------------------------------------------------------------------
# Integration — realistic full Polymarket question
# ---------------------------------------------------------------------------


def test_integration_full_polymarket_question():
    """Test a realistic Polymarket question format."""
    result = parse_market_question(
        "Will the highest temperature in New York City on April 15, 2026 "
        "be between 54 and 55 degrees Fahrenheit?"
    )
    assert result is not None
    assert result.city == "new york city"
    assert result.metric == "temperature"
    assert result.threshold == 54.0
    assert result.threshold_upper == 55.0
    assert result.unit == "F"
    assert result.direction == "range"
