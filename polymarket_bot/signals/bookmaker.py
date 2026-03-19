import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

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

    def can_evaluate(self, market: Market) -> bool:
        return market.category in ("sports", "mma", "boxing", "esports", "football", "basketball")

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
        if not self._client or not self._api_key:
            return None
        try:
            # Fetch available sports
            resp = await self._client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": self._api_key},
            )
            resp.raise_for_status()
            sports = resp.json()

            for sport in sports:
                if not sport.get("active"):
                    continue
                odds_resp = await self._client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport['key']}/odds",
                    params={
                        "apiKey": self._api_key,
                        "regions": "us",
                        "markets": "h2h",
                    },
                )
                if odds_resp.status_code != 200:
                    continue

                events = odds_resp.json()
                match = self._match_market_to_event(market, events)
                if match:
                    return match

            return None
        except Exception:
            logger.debug("Bookmaker odds fetch failed: %s", market.question)
            return None

    def _match_market_to_event(self, market: Market, events: list[dict]) -> dict | None:
        best_ratio = 0.0
        best_result = None

        for event in events:
            event_name = f"{event.get('home_team', '')} vs {event.get('away_team', '')}"
            ratio = SequenceMatcher(
                None, market.question.lower(), event_name.lower(),
            ).ratio()

            if ratio > best_ratio and ratio > 0.35:
                best_ratio = ratio
                probs = []
                for bookmaker in event.get("bookmakers", []):
                    for mkt in bookmaker.get("markets", []):
                        for outcome in mkt.get("outcomes", []):
                            price = outcome.get("price", 0)
                            if price > 0:
                                if abs(price) >= 100:
                                    probs.append(self.american_to_probability(int(price)))
                                else:
                                    probs.append(self.decimal_to_probability(price))

                if probs:
                    avg_prob = sum(probs) / len(probs)
                    best_result = {
                        "implied_probability": avg_prob,
                        "bookmakers_count": len(event.get("bookmakers", [])),
                    }

        return best_result

    @staticmethod
    def american_to_probability(american_odds: int) -> float:
        if american_odds > 0:
            return 100 / (american_odds + 100)
        return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_probability(decimal_odds: float) -> float:
        return 1 / decimal_odds if decimal_odds > 0 else 0.0
