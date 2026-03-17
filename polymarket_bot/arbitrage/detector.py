import logging
from polymarket_bot.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)


class OpportunityDetector:
    def __init__(self, min_spread: float = 0.05):
        self._min_spread = min_spread

    def check(
        self,
        polymarket_id: str,
        platform_prices: dict[str, float],
        market_ids: dict[str, str],
    ) -> ArbitrageOpportunity | None:
        poly_price = platform_prices.get("polymarket")
        if poly_price is None:
            return None

        other_prices = {k: v for k, v in platform_prices.items() if k != "polymarket"}
        if not other_prices:
            return None

        avg_other = sum(other_prices.values()) / len(other_prices)
        spread = abs(avg_other - poly_price)

        if spread < self._min_spread:
            return None

        time_sensitivity = "high" if spread > 0.15 else "medium"
        estimated_profit = spread * 100

        return ArbitrageOpportunity(
            market_ids=market_ids,
            platforms=list(platform_prices.keys()),
            prices=platform_prices,
            spread=round(spread, 4),
            estimated_profit=round(estimated_profit, 2),
            confidence=min(spread * 5, 0.95),
            time_sensitivity=time_sensitivity,
        )
