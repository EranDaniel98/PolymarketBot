import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class BookmakerSignal(SignalPlugin):
    def __init__(self, api_key: str, poll_interval: int = 60):
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "bookmaker"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        odds_data = await self._fetch_odds(market)
        if odds_data is None:
            return None

        implied_prob = odds_data["implied_probability"]
        market_price = market.current_price
        edge = implied_prob - market_price

        if abs(edge) < 0.02:
            return None

        direction = Direction.YES if edge > 0 else Direction.NO
        confidence = min(abs(edge) * 2, 0.95)

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=f"Bookmaker implied: {implied_prob:.0%} vs market: {market_price:.0%} "
                      f"(edge: {edge:+.1%}, from {odds_data['bookmakers_count']} bookmakers)",
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_odds(self, market: Market) -> dict | None:
        if not self._client:
            return None
        try:
            resp = await self._client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": self._api_key},
            )
            resp.raise_for_status()
            logger.debug("Bookmaker odds fetch — event matching not yet implemented for: %s", market.question)
            return None
        except Exception:
            logger.exception("Failed to fetch bookmaker odds")
            return None

    @staticmethod
    def american_to_probability(american_odds: int) -> float:
        if american_odds > 0:
            return 100 / (american_odds + 100)
        return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_probability(decimal_odds: float) -> float:
        return 1 / decimal_odds if decimal_odds > 0 else 0.0
