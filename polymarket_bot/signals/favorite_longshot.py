"""Favorite-Longshot Bias Signal — exploit systematic overpricing of extreme contracts."""

import logging
from datetime import datetime, timezone

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class FavoriteLongshotSignal(SignalPlugin):
    """Exploit favorite-longshot bias: contracts >92% are systematically overpriced."""

    def __init__(
        self,
        min_price_short: float = 0.92,
        max_price_long: float = 0.08,
        min_volume: float = 5000,
        min_days: int = 3,
    ):
        self._min_price_short = min_price_short
        self._max_price_long = max_price_long
        self._min_volume = min_volume
        self._min_days = min_days

    @property
    def name(self) -> str:
        return "favorite_longshot"

    @property
    def eval_interval(self) -> int | None:
        return 1800  # 30 minutes — 24h half-life, purely structural

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def can_evaluate(self, market: Market) -> bool:
        return market.current_price > 0.90 or market.current_price < 0.10

    async def evaluate(self, market: Market) -> Signal | None:
        price = market.current_price
        now = datetime.now(timezone.utc)
        days_remaining = (market.end_date - now).total_seconds() / 86400

        if days_remaining < self._min_days:
            return None
        if market.volume < self._min_volume:
            return None

        # Core: short extreme favorites
        # Academic research shows contracts >90% overestimate true probability by 3-8%.
        # Confidence scales with how extreme the price is above 90%.
        if price > self._min_price_short:
            mispricing = price - 0.90  # How far above 90% (e.g., 0.95 → 0.05)
            # Scale confidence: 2% mispricing → 0.40, 5% → 0.60, 8%+ → 0.70
            confidence = min(mispricing / 0.10 * 0.70, 0.70)
            confidence = max(confidence, 0.25)  # Floor: always at least 25% if we fire
            return Signal(
                source=self.name,
                market_id=market.id,
                direction=Direction.NO,
                confidence=round(confidence, 3),
                reasoning=f"FLB: price {price:.0%} > {self._min_price_short:.0%} "
                          f"(mispricing {mispricing:+.1%}, {days_remaining:.0f}d remaining)",
                timestamp=now,
            )

        # Mirror: buy extreme longshots (lower confidence — less documented edge)
        if self._max_price_long > price > 0.02:
            mispricing = 0.10 - price
            confidence = min(mispricing / 0.10 * 0.50, 0.50)
            confidence = max(confidence, 0.20)
            return Signal(
                source=self.name,
                market_id=market.id,
                direction=Direction.YES,
                confidence=round(confidence, 3),
                reasoning=f"FLB longshot: price {price:.0%} < {self._max_price_long:.0%} "
                          f"(mispricing {mispricing:+.1%}, {days_remaining:.0f}d remaining)",
                timestamp=now,
            )

        return None
