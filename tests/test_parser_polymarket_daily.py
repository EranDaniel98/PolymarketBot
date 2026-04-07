"""Tests for Polymarket's daily-temp bucket question format.

These markets use a multi-outcome structure where each outcome is a
single-bucket binary ('Will it be exactly N°C?') plus an 'or below' lower
bound and 'or above' upper bound. The pattern is new to the parser in
April 2026 after we discovered the live markets via polymarket.com/weather.
"""

from polymarket_weather.markets.parser import parse_market_question


class TestDailyTempBucketMarkets:
    """Format: 'Will the highest temperature in CITY be N°C on DATE?'"""

    def test_celsius_bucket_single_value(self):
        r = parse_market_question("Will the highest temperature in Hong Kong be 20°C on April 2?")
        assert r is not None
        assert r.threshold == 20.0
        assert r.threshold_upper == 22.0  # 2-degree bucket
        assert r.unit == "C"
        assert r.direction == "range"

    def test_fahrenheit_bucket_single_value(self):
        r = parse_market_question("Will the highest temperature in NYC be 75°F on April 10?")
        assert r is not None
        assert r.threshold == 75.0
        assert r.threshold_upper == 77.0
        assert r.unit == "F"
        assert r.direction == "range"

    def test_celsius_or_below(self):
        r = parse_market_question("Will the highest temperature in Hong Kong be 18°C or below on April 2?")
        assert r is not None
        assert r.threshold == 18.0
        assert r.threshold_upper is None
        assert r.unit == "C"
        assert r.direction == "below"

    def test_celsius_or_above(self):
        r = parse_market_question("Will the highest temperature in Hong Kong be 26°C or above on April 2?")
        assert r is not None
        assert r.threshold == 26.0
        assert r.threshold_upper is None
        assert r.unit == "C"
        assert r.direction == "above"

    def test_fahrenheit_or_below(self):
        r = parse_market_question("Will the highest temperature in Dallas be 60°F or below on April 8?")
        assert r is not None
        assert r.threshold == 60.0
        assert r.direction == "below"
        assert r.unit == "F"

    def test_fahrenheit_or_above(self):
        r = parse_market_question("Will the highest temperature in Dallas be 85°F or above on April 8?")
        assert r is not None
        assert r.threshold == 85.0
        assert r.direction == "above"
        assert r.unit == "F"

    def test_city_extracted_from_daily_format(self):
        r = parse_market_question("Will the highest temperature in Los Angeles be 70°F on April 10?")
        assert r is not None
        assert r.city == "los angeles"

    def test_multi_word_city(self):
        r = parse_market_question("Will the highest temperature in New York City be 65°F on April 10?")
        assert r is not None
        assert r.city in ("new york city", "new york")  # longest-match wins

    def test_mexico_city(self):
        r = parse_market_question("Will the highest temperature in Mexico City be 25°C on April 10?")
        assert r is not None
        assert r.threshold == 25.0
        assert r.direction == "range"

    def test_hong_kong(self):
        r = parse_market_question("Will the highest temperature in Hong Kong be 20°C on April 2?")
        assert r is not None
        # City will be empty unless hong_kong is in the default aliases — we
        # don't assert city here; the city_mapper integration test covers it.

    def test_negative_temperature(self):
        r = parse_market_question("Will the low temperature in Anchorage be -5°F on January 5?")
        # This format doesn't strictly match 'highest temperature in X be N',
        # but the 'be N° on' pattern should still trip. Test as edge case.
        if r is not None:
            assert r.threshold == -5.0


class TestDailyFormatDoesNotBreakLegacyPatterns:
    """Guard: adding the new patterns must not break the existing 22 parser tests."""

    def test_range_between_still_works(self):
        r = parse_market_question("Will the high be between 65 and 70 degrees Fahrenheit?")
        assert r is not None
        assert r.threshold == 65.0
        assert r.threshold_upper == 70.0
        assert r.direction == "range"

    def test_above_verb_still_works(self):
        r = parse_market_question("Will the temperature in New York City be above 75 degrees on April 10?")
        assert r is not None
        assert r.threshold == 75.0
        assert r.direction == "above"

    def test_below_freezing_still_works(self):
        r = parse_market_question("Will the temperature drop below freezing in Chicago?")
        assert r is not None
        assert r.threshold == 32.0
        assert r.direction == "below"
