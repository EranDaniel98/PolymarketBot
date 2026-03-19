"""Cross-Platform Divergence Signal — detect sustained divergence between forecasting platforms."""

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

from polymarket_bot.data_sources.metaculus import MetaculusClient, MetaculusForecast, get_metaculus_client
from polymarket_bot.data_sources.manifold import ManifoldClient
from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class DivergenceSignal(SignalPlugin):
    """Detect sustained divergence between Polymarket and other forecasting platforms."""

    def __init__(
        self,
        min_divergence: float = 0.08,
        min_forecasters: int = 50,
        min_days: int = 3,
    ):
        self._min_divergence = min_divergence
        self._min_forecasters = min_forecasters
        self._min_days = min_days
        self._metaculus: MetaculusClient | None = None
        self._manifold: ManifoldClient | None = None

    @property
    def name(self) -> str:
        return "divergence"

    async def start(self) -> None:
        self._metaculus = await get_metaculus_client()
        self._manifold = ManifoldClient()
        await self._manifold.start()

    async def stop(self) -> None:
        if self._manifold:
            await self._manifold.stop()

    async def evaluate(self, market: Market) -> Signal | None:
        now = datetime.now(timezone.utc)
        days_left = (market.end_date - now).total_seconds() / 86400
        if days_left < self._min_days:
            return None

        # Search both platforms
        metaculus_matches = await self._metaculus.search(market.question, limit=3) if self._metaculus else []
        manifold_matches = await self._manifold.search(market.question, limit=3) if self._manifold else []

        best_metaculus = self._find_best_match(market.question, metaculus_matches)
        best_manifold = self._find_best_manifold(market.question, manifold_matches)

        divergences = []

        if best_metaculus and best_metaculus.forecaster_count >= self._min_forecasters:
            if best_metaculus.community_prediction is not None:
                div = best_metaculus.community_prediction - market.current_price
                if abs(div) >= self._min_divergence:
                    divergences.append(("Metaculus", div, best_metaculus.forecaster_count))

        if best_manifold:
            div = best_manifold.probability - market.current_price
            if abs(div) >= self._min_divergence:
                divergences.append(("Manifold", div, 0))

        if not divergences:
            return None

        # Average divergence across agreeing platforms
        avg_div = sum(d[1] for d in divergences) / len(divergences)
        if abs(avg_div) < self._min_divergence:
            return None

        direction = Direction.YES if avg_div > 0 else Direction.NO
        confidence = min(abs(avg_div) * 1.5, 0.65)

        # Boost if multiple platforms agree
        if len(divergences) >= 2:
            # Both Metaculus and Manifold disagree with Polymarket
            confidence = min(confidence * 1.2, 0.75)

        sources = ", ".join(f"{d[0]} ({d[1]:+.0%})" for d in divergences)
        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=f"Divergence: {sources} vs Polymarket {market.current_price:.0%}",
            timestamp=now,
        )

    def _find_best_match(self, question: str, forecasts: list[MetaculusForecast]) -> MetaculusForecast | None:
        best = None
        best_ratio = 0.0
        q_lower = question.lower()
        for f in forecasts:
            ratio = SequenceMatcher(None, q_lower, f.question.lower()).ratio()
            if ratio > best_ratio and ratio > 0.3:
                best_ratio = ratio
                best = f
        return best

    def _find_best_manifold(self, question: str, markets: list) -> object | None:
        best = None
        best_ratio = 0.0
        q_lower = question.lower()
        for m in markets:
            ratio = SequenceMatcher(None, q_lower, m.question.lower()).ratio()
            if ratio > best_ratio and ratio > 0.3:
                best_ratio = ratio
                best = m
        return best
