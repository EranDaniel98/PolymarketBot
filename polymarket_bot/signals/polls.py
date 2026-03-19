import logging
import re
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class PollSignal(SignalPlugin):
    def __init__(self, poll_interval: int = 3600):
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "polls"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    def can_evaluate(self, market: Market) -> bool:
        return market.category in ("politics", "election", "policy")

    async def evaluate(self, market: Market) -> Signal | None:
        poll_data = await self._fetch_poll_data(market)
        if poll_data is None:
            return None

        implied_prob = poll_data["implied_probability"]
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
            reasoning=f"Poll implied: {implied_prob:.0%} vs market: {market_price:.0%} "
                      f"(edge: {edge:+.1%}). Source: {poll_data.get('source', 'unknown')}",
            timestamp=datetime.now(timezone.utc),
        )

    def _extract_search_terms(self, question: str) -> str:
        # Remove common filler words, keep meaningful terms
        stop_words = {
            "will", "the", "be", "in", "of", "to", "a", "an", "is", "by",
            "win", "for", "on", "at", "and", "or", "this", "that", "who",
        }
        words = re.findall(r'\b[A-Za-z]+\b', question)
        keywords = [w for w in words if w.lower() not in stop_words and len(w) > 2]
        return " ".join(keywords[:5])

    async def _fetch_poll_data(self, market: Market) -> dict | None:
        if not self._client:
            return None
        if market.category not in ("politics", "election", "policy"):
            return None

        keywords = self._extract_search_terms(market.question)
        if not keywords:
            return None

        try:
            # Try RealClearPolitics polling search
            resp = await self._client.get(
                "https://www.realclearpolling.com/api/polls/search",
                params={"q": keywords},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    avg = data[0].get("rcp_average", data[0].get("average", 0))
                    if avg and avg > 0:
                        return {
                            "implied_probability": avg / 100,
                            "source": "RCP Average",
                        }

            # Fallback: try 270toWin aggregate
            resp2 = await self._client.get(
                "https://www.270towin.com/api/polls",
                params={"q": keywords},
            )
            if resp2.status_code == 200:
                data2 = resp2.json()
                if data2 and isinstance(data2, list) and len(data2) > 0:
                    avg = data2[0].get("average", 0)
                    if avg and avg > 0:
                        return {
                            "implied_probability": avg / 100,
                            "source": "270toWin",
                        }

            return None
        except Exception:
            logger.debug("Poll data fetch failed for: %s", market.question)
            return None
