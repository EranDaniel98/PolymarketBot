"""Smart Market Filtering — score and rank markets by edge potential."""

import logging
from datetime import datetime, timezone

from polymarket_bot.models import Market

logger = logging.getLogger(__name__)


class MarketFilter:
    def __init__(
        self,
        min_price: float = 0.05,
        max_price: float = 0.95,
        max_days_to_end: int = 365,
        min_days_to_end: int = 1,
    ):
        self._min_price = min_price
        self._max_price = max_price
        self._max_days_to_end = max_days_to_end
        self._min_days_to_end = min_days_to_end

    def filter_and_rank(self, markets: list[Market]) -> list[Market]:
        """Filter out low-quality markets and rank by edge potential."""
        scored = []
        for market in markets:
            score = self._score_market(market)
            if score > 0:
                scored.append((score, market))

        scored.sort(key=lambda x: x[0], reverse=True)
        filtered = [m for _, m in scored]
        logger.info("Filtered %d -> %d markets", len(markets), len(filtered))
        return filtered

    def _score_market(self, market: Market) -> float:
        score = 0.0

        # Filter: must have tokens
        if not market.tokens:
            return 0.0

        # Filter: price must be in tradeable range
        # Allow extreme prices through for FLB strategy (targets > 0.92 and < 0.08)
        p = market.current_price
        if p < 0.01 or p > 0.99:
            return 0.0

        # Score: prefer markets where price leans but might be wrong (0.15-0.40, 0.60-0.85).
        # Markets at 0.50 are maximally efficient — hardest to find edge.
        if (0.15 <= p <= 0.40) or (0.60 <= p <= 0.85):
            score += 30  # Best edge potential — market has a lean that may be wrong
        elif 0.40 < p < 0.60:
            score += 15  # Efficient range — less likely to find edge
        elif (p > 0.90 or p < 0.10) and market.volume >= 5000:
            score += 20  # FLB territory — extreme prices with liquidity
        else:
            score += 5   # Very extreme or low volume — some edge but risky

        # Score: time to resolution — prefer markets resolving soon but not too soon
        now = datetime.now(timezone.utc)
        days_remaining = (market.end_date - now).total_seconds() / 86400

        if days_remaining < self._min_days_to_end:
            return 0.0  # Too close to end, too risky
        if days_remaining > self._max_days_to_end:
            return 0.0  # Too far out, hard to predict

        # Sweet spot: 3-30 days out
        if 3 <= days_remaining <= 30:
            score += 25
        elif 1 <= days_remaining <= 3:
            score += 15  # Very close to end — signals should be strong
        else:
            score += 10

        # Score: category bonus — some categories have more signal sources
        category_scores = {
            "politics": 10,
            "election": 10,
            "crypto": 8,
            "sports": 8,
            "science": 5,
            "tech": 5,
        }
        score += category_scores.get(market.category, 3)

        # Score: question length — longer questions tend to be more specific = better for signals
        if len(market.question) > 50:
            score += 5

        return score
