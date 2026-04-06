"""Tests for the weather market scanner (polymarket_weather.markets.scanner)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from polymarket_weather.markets.scanner import (
    parse_clob_tokens,
    parse_outcome_prices,
    parse_gamma_event,
    WeatherMarketScanner,
)


# ---------------------------------------------------------------------------
# parse_clob_tokens
# ---------------------------------------------------------------------------

def test_parse_clob_tokens_json_string():
    tokens = parse_clob_tokens('["tok_yes","tok_no"]')
    assert tokens == ("tok_yes", "tok_no")


def test_parse_clob_tokens_list():
    tokens = parse_clob_tokens(["tok_yes", "tok_no"])
    assert tokens == ("tok_yes", "tok_no")


def test_parse_clob_tokens_list_of_dicts():
    tokens = parse_clob_tokens([{"token_id": "a"}, {"token_id": "b"}])
    assert tokens == ("a", "b")


def test_parse_clob_tokens_list_of_dicts_id_key():
    tokens = parse_clob_tokens([{"id": "x"}, {"id": "y"}])
    assert tokens == ("x", "y")


def test_parse_clob_tokens_invalid_none():
    assert parse_clob_tokens(None) is None


def test_parse_clob_tokens_invalid_string():
    assert parse_clob_tokens("invalid") is None


def test_parse_clob_tokens_empty_list():
    assert parse_clob_tokens([]) is None


def test_parse_clob_tokens_single_element():
    assert parse_clob_tokens(["only_one"]) is None


# ---------------------------------------------------------------------------
# parse_outcome_prices
# ---------------------------------------------------------------------------

def test_parse_outcome_prices_json_string():
    assert parse_outcome_prices('["0.65","0.35"]') == (0.65, 0.35)


def test_parse_outcome_prices_list():
    assert parse_outcome_prices([0.7, 0.3]) == (0.7, 0.3)


def test_parse_outcome_prices_list_of_strings():
    assert parse_outcome_prices(["0.45", "0.55"]) == (0.45, 0.55)


def test_parse_outcome_prices_default_none():
    assert parse_outcome_prices(None) == (0.5, 0.5)


def test_parse_outcome_prices_default_invalid_string():
    assert parse_outcome_prices("not-json") == (0.5, 0.5)


def test_parse_outcome_prices_default_wrong_type():
    assert parse_outcome_prices(42) == (0.5, 0.5)


# ---------------------------------------------------------------------------
# parse_gamma_event
# ---------------------------------------------------------------------------

SAMPLE_EVENT = {
    "id": "evt_123",
    "markets": [
        {
            "conditionId": "0xabc",
            "question": "Will the high temp in NYC be between 50 and 54 degrees Fahrenheit?",
            "clobTokenIds": '["tok_yes_1","tok_no_1"]',
            "outcomePrices": '["0.35","0.65"]',
            "active": True,
            "closed": False,
            "endDateIso": "2026-04-16T00:00:00Z",
            "resolutionSource": "Weather Underground",
            "volume": "50000",
            "slug": "nyc-temp-50-54f",
        },
        {
            "conditionId": "0xdef",
            "question": "Will the high temp in NYC be 55 degrees Fahrenheit or above?",
            "clobTokenIds": '["tok_yes_2","tok_no_2"]',
            "outcomePrices": '["0.45","0.55"]',
            "active": True,
            "closed": False,
            "endDateIso": "2026-04-16T00:00:00Z",
        },
        {
            "conditionId": "0xghi",
            "question": "Closed market",
            "active": False,
            "closed": True,
        },
    ],
    "tags": [{"label": "weather", "id": 42}],
}


def test_parse_gamma_event_basic():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert len(markets) == 2  # Skips the closed/inactive market


def test_parse_gamma_event_ids():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].market_id == "0xabc"
    assert markets[0].event_id == "evt_123"


def test_parse_gamma_event_tokens():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].yes_token_id == "tok_yes_1"
    assert markets[0].no_token_id == "tok_no_1"


def test_parse_gamma_event_prices():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].current_price == 0.35
    assert markets[0].no_price == 0.65


def test_parse_gamma_event_resolution_source():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].resolution_source == "Weather Underground"


def test_parse_gamma_event_end_date():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].end_date is not None
    assert markets[0].end_date.year == 2026
    assert markets[0].end_date.month == 4
    assert markets[0].end_date.day == 16


def test_parse_gamma_event_category():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].category == "weather"


def test_parse_gamma_event_volume():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].volume == 50000.0


def test_parse_gamma_event_slug():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[0].slug == "nyc-temp-50-54f"


def test_parse_gamma_event_empty_markets():
    markets = parse_gamma_event({"id": "x", "markets": []})
    assert len(markets) == 0


def test_parse_gamma_event_no_tokens_skipped():
    event = {
        "id": "x",
        "markets": [{"conditionId": "0x1", "active": True, "closed": False}],
    }
    markets = parse_gamma_event(event)
    assert len(markets) == 0  # Skipped because no tokens


def test_parse_gamma_event_second_market_event_id():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert markets[1].event_id == "evt_123"


def test_parse_gamma_event_end_date_fallback_to_enddate_field():
    event = {
        "id": "e1",
        "markets": [
            {
                "conditionId": "0x1",
                "question": "Q?",
                "clobTokenIds": '["a","b"]',
                "outcomePrices": '["0.5","0.5"]',
                "active": True,
                "closed": False,
                "endDate": "2026-06-01T12:00:00Z",
            }
        ],
    }
    markets = parse_gamma_event(event)
    assert markets[0].end_date is not None
    assert markets[0].end_date.month == 6


def test_parse_gamma_event_no_end_date():
    event = {
        "id": "e1",
        "markets": [
            {
                "conditionId": "0x1",
                "question": "Q?",
                "clobTokenIds": '["a","b"]',
                "outcomePrices": '["0.5","0.5"]',
                "active": True,
                "closed": False,
            }
        ],
    }
    markets = parse_gamma_event(event)
    assert markets[0].end_date is None


# ---------------------------------------------------------------------------
# WeatherMarketScanner
# ---------------------------------------------------------------------------

def _make_http_response(data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


@pytest.fixture
def scanner():
    return WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=False,
    )


@pytest.mark.asyncio
async def test_scanner_fetch_weather_markets_with_keyword_filter(scanner):
    events = [
        {
            "id": "evt_1",
            "markets": [
                {
                    "conditionId": "0x1",
                    "question": "Will temperature exceed 90 degrees?",
                    "clobTokenIds": '["yes1","no1"]',
                    "outcomePrices": '["0.6","0.4"]',
                    "active": True,
                    "closed": False,
                }
            ],
            "tags": [],
        },
        {
            "id": "evt_2",
            "markets": [
                {
                    "conditionId": "0x2",
                    "question": "Will the stock hit $100?",
                    "clobTokenIds": '["yes2","no2"]',
                    "outcomePrices": '["0.3","0.7"]',
                    "active": True,
                    "closed": False,
                }
            ],
            "tags": [],
        },
    ]

    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response(events))
    markets = await scanner.fetch_weather_markets()

    # Only the first market matches weather keywords ("temperature", "degrees")
    assert len(markets) == 1
    assert markets[0].market_id == "0x1"
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_fetch_with_tag_id_no_keyword_filter():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=False,
    )
    scanner._weather_tag_id = 42  # Simulate tag already discovered

    events = [
        {
            "id": "evt_1",
            "markets": [
                {
                    "conditionId": "0x1",
                    "question": "Will it rain?",
                    "clobTokenIds": '["yes1","no1"]',
                    "outcomePrices": '["0.4","0.6"]',
                    "active": True,
                    "closed": False,
                }
            ],
            "tags": [{"label": "weather"}],
        },
    ]

    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response(events))
    markets = await scanner.fetch_weather_markets()

    assert len(markets) == 1
    assert markets[0].market_id == "0x1"
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_returns_empty_on_http_error(scanner):
    await scanner.start()
    scanner._http.get = AsyncMock(side_effect=Exception("Network error"))
    markets = await scanner.fetch_weather_markets()
    assert markets == []
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_returns_empty_on_non_200(scanner):
    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response([], status_code=500))
    markets = await scanner.fetch_weather_markets()
    assert markets == []
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_respects_max_markets():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=False,
        max_markets=2,
    )
    scanner._weather_tag_id = 42  # Use tag filter (no keyword filtering)

    events = [
        {
            "id": f"evt_{i}",
            "markets": [
                {
                    "conditionId": f"0x{i}",
                    "question": f"Weather question {i}?",
                    "clobTokenIds": f'["yes{i}","no{i}"]',
                    "outcomePrices": '["0.5","0.5"]',
                    "active": True,
                    "closed": False,
                }
            ],
            "tags": [],
        }
        for i in range(10)
    ]

    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response(events))
    markets = await scanner.fetch_weather_markets()
    assert len(markets) == 2
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_discover_weather_tag_by_label():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=True,
    )
    tags = [
        {"id": 1, "label": "Politics", "slug": "politics"},
        {"id": 7, "label": "Weather", "slug": "weather"},
        {"id": 3, "label": "Sports", "slug": "sports"},
    ]

    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response(tags))
    await scanner._discover_weather_tag()
    assert scanner._weather_tag_id == 7
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_discover_weather_tag_by_slug():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=True,
    )
    tags = [
        {"id": 99, "label": "Meteorology", "slug": "weather"},
    ]

    await scanner.start()
    scanner._http.get = AsyncMock(return_value=_make_http_response(tags))
    await scanner._discover_weather_tag()
    assert scanner._weather_tag_id == 99
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_tag_discovery_failure_leaves_none():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=True,
    )

    await scanner.start()
    scanner._http.get = AsyncMock(side_effect=Exception("timeout"))
    await scanner._discover_weather_tag()
    assert scanner._weather_tag_id is None
    await scanner.stop()


@pytest.mark.asyncio
async def test_scanner_stop_without_start():
    scanner = WeatherMarketScanner(
        gamma_api_url="https://gamma-api.example.com",
        weather_tag_discovery=False,
    )
    # Should not raise
    await scanner.stop()
