import logging
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

    async def _fetch_poll_data(self, market: Market) -> dict | None:
        if not self._client:
            return None
        if market.category not in ("politics", "election", "policy"):
            return None
        try:
            logger.debug("Poll fetch not yet connected to live source for: %s", market.question)
            return None
        except Exception:
            logger.exception("Failed to fetch poll data")
            return None
