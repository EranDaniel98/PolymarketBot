import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from polymarket_bot.signals.weather import WeatherSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def weather():
    sig = WeatherSignal()
    sig._http = AsyncMock()
    return sig


def _make_market(question="Will NYC high temp be 40-45°F?", price=0.20):
    return Market(
        id="m1", question=question,
        end_date=datetime.now(timezone.utc) + timedelta(days=1),
        tokens={"YES": "a", "NO": "b"}, current_price=price,
        category="weather", volume=5000,
    )


def _mock_forecast_response(temp_f: float):
    """Create a mock NWS forecast API response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "properties": {
            "periods": [
                {
                    "name": "Today",
                    "isDaytime": True,
                    "temperature": temp_f,
                    "temperatureUnit": "F",
                },
            ]
        }
    }
    return resp


def test_can_evaluate_weather_market(weather):
    assert weather.can_evaluate(_make_market()) is True


def test_cannot_evaluate_politics_market(weather):
    m = _make_market(question="Will Biden win?")
    assert weather.can_evaluate(m) is False


def test_parse_temperature_range():
    sig = WeatherSignal()
    assert sig._parse_temperature_range("Will NYC high temp be 40-45°F?") == (40, 45)
    assert sig._parse_temperature_range("Temperature between 55 and 60 degrees") == (55, 60)
    assert sig._parse_temperature_range("Will it be above 80 degrees?") == (80, 150)
    assert sig._parse_temperature_range("Temp below 32°F?") == (-50, 32)
    assert sig._parse_temperature_range("Random question") is None


def test_detect_city():
    sig = WeatherSignal()
    assert sig._detect_city("will nyc high temp be 40-45°f?") == ("OKX", 33, 37)
    assert sig._detect_city("los angeles temperature") == ("LOX", 154, 44)
    assert sig._detect_city("random city forecast") is None


async def test_signal_when_forecast_in_range(weather):
    """Forecast of 42°F falls in 40-45 range, market underpriced at 0.20 → YES signal."""
    weather._http.get.return_value = _mock_forecast_response(42.0)

    market = _make_market(price=0.20)
    signal = await weather.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.YES
    assert signal.confidence > 0.40


async def test_signal_when_forecast_outside_range(weather):
    """Forecast of 55°F is outside 40-45 range, market at 0.50 → NO signal."""
    weather._http.get.return_value = _mock_forecast_response(55.0)

    market = _make_market(price=0.50)
    signal = await weather.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.NO


async def test_no_signal_when_forecast_near_boundary(weather):
    """Forecast of 45.5°F — too close to range boundary, should not signal."""
    weather._http.get.return_value = _mock_forecast_response(45.5)

    market = _make_market(price=0.50)
    signal = await weather.evaluate(market)
    # Either None or NO with low confidence (boundary is within 1°F)
    if signal is not None:
        assert signal.confidence < 0.50


async def test_no_signal_non_weather_market(weather):
    market = _make_market(question="Will Bitcoin reach $100K?")
    signal = await weather.evaluate(market)
    assert signal is None


async def test_forecast_uses_gridpoint_api(weather):
    """Verify the forecast endpoint is called, not observations."""
    weather._http.get.return_value = _mock_forecast_response(42.0)

    market = _make_market(price=0.20)
    await weather.evaluate(market)

    # Should call the gridpoints forecast URL, not stations/observations
    call_url = weather._http.get.call_args[0][0]
    assert "gridpoints" in call_url
    assert "forecast" in call_url
    assert "observations" not in call_url


async def test_forecast_celsius_conversion(weather):
    """Forecast in Celsius should be converted to Fahrenheit."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "properties": {
            "periods": [
                {
                    "name": "Today",
                    "isDaytime": True,
                    "temperature": 5.56,  # ~42°F
                    "temperatureUnit": "C",
                },
            ]
        }
    }
    weather._http.get.return_value = resp

    market = _make_market(price=0.20)
    signal = await weather.evaluate(market)
    assert signal is not None
    assert signal.direction == Direction.YES
