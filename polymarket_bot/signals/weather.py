"""Weather Market Signal — compare official forecasts to Polymarket weather prices.

Documented edge: one bot turned $204 into $24,000 with 73% win rate on weather markets.
Strategy: NOAA/weather API forecasts are far more accurate than crowd-priced weather
markets. When the forecast clearly falls in a specific range but Polymarket underprices
that range, buy it. Hedge by shorting neighboring ranges.
"""

import logging
import re
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

# Weather-related keywords to identify weather markets
WEATHER_KEYWORDS = [
    "temperature", "temp", "degrees", "fahrenheit", "celsius",
    "rain", "rainfall", "precipitation", "snow", "snowfall",
    "wind", "mph", "weather", "high temp", "low temp",
    "humidity", "heat", "cold", "freeze", "frost",
]

# Cities with NOAA station IDs for reliable forecasts
CITY_STATIONS = {
    "new york": "KNYC",
    "nyc": "KNYC",
    "los angeles": "KLAX",
    "la": "KLAX",
    "chicago": "KORD",
    "miami": "KMIA",
    "london": "EGLL",
    "washington": "KDCA",
    "dc": "KDCA",
    "san francisco": "KSFO",
    "boston": "KBOS",
    "seattle": "KSEA",
    "denver": "KDEN",
    "atlanta": "KATL",
    "dallas": "KDFW",
    "houston": "KIAH",
    "phoenix": "KPHX",
    "philadelphia": "KPHL",
    "detroit": "KDTW",
    "minneapolis": "KMSP",
}


class WeatherSignal(SignalPlugin):
    """Compare weather forecasts to Polymarket temperature/weather market prices."""

    def __init__(self):
        self._http: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "weather"

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    def can_evaluate(self, market: Market) -> bool:
        q = market.question.lower()
        return any(kw in q for kw in WEATHER_KEYWORDS)

    async def evaluate(self, market: Market) -> Signal | None:
        if not self._http:
            return None

        q = market.question.lower()

        # Parse what the market is asking about
        temp_range = self._parse_temperature_range(market.question)
        city = self._detect_city(q)

        if not city or not temp_range:
            return None

        # Get forecast from weather API
        forecast_temp = await self._get_forecast(city)
        if forecast_temp is None:
            return None

        low, high = temp_range
        forecast_in_range = low <= forecast_temp <= high

        # Calculate edge based on how far forecast is from range boundaries
        if forecast_in_range:
            # Forecast is IN this range — if market underprices it, buy YES
            # Confidence based on how centrally the forecast falls in the range
            range_center = (low + high) / 2
            range_width = high - low
            if range_width <= 0:
                return None
            centrality = 1.0 - abs(forecast_temp - range_center) / (range_width / 2)
            centrality = max(0.0, min(1.0, centrality))

            if market.current_price < 0.60:  # Only if market underprices
                confidence = min(0.40 + centrality * 0.35, 0.75)
                return Signal(
                    source=self.name,
                    market_id=market.id,
                    direction=Direction.YES,
                    confidence=round(confidence, 3),
                    reasoning=f"Weather: forecast {forecast_temp}°F in range [{low}-{high}], "
                              f"market {market.current_price:.0%} (underpriced)",
                    timestamp=datetime.now(timezone.utc),
                )
        else:
            # Forecast is OUTSIDE this range — if market overprices it, buy NO
            distance = min(abs(forecast_temp - low), abs(forecast_temp - high))
            if distance < 2:
                return None  # Too close to boundary, uncertain

            if market.current_price > 0.30:  # Only if market overprices
                # Higher confidence the further the forecast is from the range
                confidence = min(0.35 + distance * 0.03, 0.70)
                return Signal(
                    source=self.name,
                    market_id=market.id,
                    direction=Direction.NO,
                    confidence=round(confidence, 3),
                    reasoning=f"Weather: forecast {forecast_temp}°F outside range [{low}-{high}] "
                              f"(distance: {distance}°F), market {market.current_price:.0%} (overpriced)",
                    timestamp=datetime.now(timezone.utc),
                )

        return None

    def _parse_temperature_range(self, question: str) -> tuple[float, float] | None:
        """Extract temperature range from market question.

        Examples:
        - "Will NYC high temp be 40-45°F?" → (40, 45)
        - "Temperature above 80 degrees" → (80, 150)
        - "Between 55 and 60 degrees" → (55, 60)
        """
        # Pattern: "X-Y°F" or "X to Y degrees" or "between X and Y"
        patterns = [
            r'(\d+)\s*[-–]\s*(\d+)\s*[°]?\s*[fF]',
            r'(\d+)\s*to\s*(\d+)\s*(?:degrees|°)',
            r'between\s*(\d+)\s*and\s*(\d+)',
            r'(\d+)\s*[-–]\s*(\d+)\s*degrees',
        ]
        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                return (float(match.group(1)), float(match.group(2)))

        # Pattern: "above X" or "over X"
        above = re.search(r'(?:above|over|higher than|more than)\s*(\d+)', question, re.I)
        if above:
            return (float(above.group(1)), 150.0)

        # Pattern: "below X" or "under X"
        below = re.search(r'(?:below|under|lower than|less than)\s*(\d+)', question, re.I)
        if below:
            return (-50.0, float(below.group(1)))

        return None

    def _detect_city(self, question_lower: str) -> str | None:
        for city_name, station in CITY_STATIONS.items():
            if city_name in question_lower:
                return station
        return None

    async def _get_forecast(self, station: str) -> float | None:
        """Fetch forecast temperature from weather.gov API (free, no auth)."""
        if not self._http:
            return None
        try:
            # NWS API: get forecast for station
            resp = await self._http.get(
                f"https://api.weather.gov/stations/{station}/observations/latest",
                headers={"Accept": "application/geo+json"},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            props = data.get("properties", {})
            temp_c = props.get("temperature", {}).get("value")
            if temp_c is None:
                return None

            # Convert Celsius to Fahrenheit (Polymarket weather markets use °F)
            return round(temp_c * 9 / 5 + 32, 1)
        except Exception:
            logger.debug("Weather forecast fetch failed for station %s", station)
            return None
