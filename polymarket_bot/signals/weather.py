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

# Cities mapped to NWS forecast grid coordinates (office/gridX,gridY)
# These are used to fetch forecasts from api.weather.gov/gridpoints/{office}/{x},{y}/forecast
CITY_GRIDS = {
    "new york": ("OKX", 33, 37),
    "nyc": ("OKX", 33, 37),
    "los angeles": ("LOX", 154, 44),
    "la": ("LOX", 154, 44),
    "chicago": ("LOT", 65, 76),
    "miami": ("MFL", 110, 50),
    "washington": ("LWX", 97, 71),
    "dc": ("LWX", 97, 71),
    "san francisco": ("MTR", 85, 105),
    "boston": ("BOX", 71, 90),
    "seattle": ("SEW", 124, 67),
    "denver": ("BOU", 62, 60),
    "atlanta": ("FFC", 50, 86),
    "dallas": ("FWD", 80, 103),
    "houston": ("HGX", 65, 97),
    "phoenix": ("PSR", 159, 57),
    "philadelphia": ("PHI", 57, 97),
    "detroit": ("DTX", 65, 33),
    "minneapolis": ("MPX", 107, 71),
}


class WeatherSignal(SignalPlugin):
    """Compare weather forecasts to Polymarket temperature/weather market prices."""

    def __init__(self):
        self._http: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "weather"

    @property
    def eval_interval(self) -> int | None:
        return 1800  # 30 minutes — 12h half-life, NOAA updates 4x/day

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

    def _detect_city(self, question_lower: str) -> tuple[str, int, int] | None:
        for city_name, grid in CITY_GRIDS.items():
            if city_name in question_lower:
                return grid
        return None

    async def _get_forecast(self, grid: tuple[str, int, int]) -> float | None:
        """Fetch forecast high temperature from NWS gridpoints API (free, no auth).

        Uses the forecast endpoint which returns multi-day forecasts,
        not the observations endpoint which only shows current conditions.
        """
        if not self._http:
            return None
        office, grid_x, grid_y = grid
        try:
            resp = await self._http.get(
                f"https://api.weather.gov/gridpoints/{office}/{grid_x},{grid_y}/forecast",
                headers={"Accept": "application/geo+json"},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            periods = data.get("properties", {}).get("periods", [])
            if not periods:
                return None

            # Use the first daytime period's temperature (today/tomorrow high)
            for period in periods[:4]:
                if period.get("isDaytime", True):
                    temp = period.get("temperature")
                    unit = period.get("temperatureUnit", "F")
                    if temp is not None:
                        if unit == "C":
                            return round(temp * 9 / 5 + 32, 1)
                        return float(temp)

            return None
        except Exception:
            logger.debug("Weather forecast fetch failed for grid %s/%d,%d", office, grid_x, grid_y)
            return None
