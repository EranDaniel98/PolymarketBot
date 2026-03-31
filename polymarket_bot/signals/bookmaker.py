import logging
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

# Cache TTLs
_SPORTS_CACHE_TTL = 43200  # 12 hours — sport list rarely changes
_ODDS_CACHE_TTL = 600      # 10 minutes — odds update between games


class BookmakerSignal(SignalPlugin):
    def __init__(self, api_key: str, poll_interval: int = 60):
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None
        # Caches to avoid burning free API tier (500 req/month)
        self._sports_cache: tuple[float, list] | None = None
        self._odds_cache: dict[str, tuple[float, list]] = {}  # sport_key → (ts, events)
        self._market_sport_map: dict[str, str | None] = {}    # market_id → sport_key

    @property
    def name(self) -> str:
        return "bookmaker"

    @property
    def eval_interval(self) -> int | None:
        return 600  # 10 minutes — 3h half-life, odds update slowly

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

    async def _get_cached_sports(self) -> list:
        """Get cached sports list, refreshing every 12 hours."""
        now = time.time()
        if self._sports_cache and (now - self._sports_cache[0]) < _SPORTS_CACHE_TTL:
            return self._sports_cache[1]

        if not self._client or not self._api_key:
            return []
        try:
            resp = await self._client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": self._api_key},
            )
            resp.raise_for_status()
            sports = resp.json()
            self._sports_cache = (now, sports)
            return sports
        except Exception:
            logger.debug("Failed to fetch sports list")
            return self._sports_cache[1] if self._sports_cache else []

    async def _get_cached_sport_odds(self, sport_key: str) -> list:
        """Get cached odds for a sport, refreshing every 10 minutes."""
        now = time.time()
        cached = self._odds_cache.get(sport_key)
        if cached and (now - cached[0]) < _ODDS_CACHE_TTL:
            return cached[1]

        if not self._client or not self._api_key:
            return []
        try:
            resp = await self._client.get(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": "us",
                    "markets": "h2h",
                },
            )
            if resp.status_code != 200:
                return cached[1] if cached else []
            events = resp.json()
            self._odds_cache[sport_key] = (now, events)
            return events
        except Exception:
            logger.debug("Failed to fetch odds for %s", sport_key)
            return cached[1] if cached else []

    async def _fetch_odds(self, market: Market) -> dict | None:
        if not self._client or not self._api_key:
            return None

        # Check if market is already mapped to a sport
        if market.id in self._market_sport_map:
            sport_key = self._market_sport_map[market.id]
            if sport_key is None:
                return None  # Previously failed to match
            events = await self._get_cached_sport_odds(sport_key)
            return self._match_market_to_event(market, events)

        # First time: search through sports to find a match
        try:
            sports = await self._get_cached_sports()
            for sport in sports:
                if not sport.get("active"):
                    continue
                events = await self._get_cached_sport_odds(sport["key"])
                match = self._match_market_to_event(market, events)
                if match:
                    self._market_sport_map[market.id] = sport["key"]
                    return match

            # No match found — cache the negative result
            self._market_sport_map[market.id] = None
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

                yes_team = self._identify_yes_outcome(
                    market.question, event.get("home_team", ""), event.get("away_team", ""),
                )

                probs = []
                for bookmaker in event.get("bookmakers", []):
                    for mkt in bookmaker.get("markets", []):
                        for outcome in mkt.get("outcomes", []):
                            outcome_name = outcome.get("name", "")
                            price = outcome.get("price", 0)
                            if price == 0:
                                continue
                            if yes_team and outcome_name.lower() != yes_team.lower():
                                continue
                            if abs(price) >= 100:
                                probs.append(self.american_to_probability(int(price)))
                            else:
                                probs.append(self.decimal_to_probability(price))

                if probs:
                    avg_prob = sum(probs) / len(probs)
                    avg_prob = min(avg_prob / 1.05, 0.99)
                    best_result = {
                        "implied_probability": avg_prob,
                        "bookmakers_count": len(event.get("bookmakers", [])),
                    }

        return best_result

    @staticmethod
    def _identify_yes_outcome(question: str, home_team: str, away_team: str) -> str | None:
        q = question.lower()
        home = home_team.lower()
        away = away_team.lower()

        home_in_q = home in q if home else False
        away_in_q = away in q if away else False

        if home_in_q and not away_in_q:
            return home_team
        if away_in_q and not home_in_q:
            return away_team

        for word in home.split():
            if len(word) > 3 and word in q:
                return home_team
        for word in away.split():
            if len(word) > 3 and word in q:
                return away_team

        if home_team:
            return home_team
        return None

    @staticmethod
    def american_to_probability(american_odds: int) -> float:
        if american_odds > 0:
            return 100 / (american_odds + 100)
        return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_probability(decimal_odds: float) -> float:
        return 1 / decimal_odds if decimal_odds > 0 else 0.0
