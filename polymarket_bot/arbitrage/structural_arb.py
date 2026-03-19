"""Same-market structural arbitrage — buy YES + NO when combined < $1.00 minus fees."""

import logging
from dataclasses import dataclass

from polymarket_bot.models import Market

logger = logging.getLogger(__name__)


@dataclass
class StructuralArbOpportunity:
    market_id: str
    yes_price: float
    no_price: float
    combined_price: float
    expected_profit_pct: float
    tokens: dict[str, str]


class StructuralArbDetector:
    def __init__(self, fee_rate: float = 0.02, min_profit_pct: float = 0.005):
        self._fee_rate = fee_rate
        self._min_profit_pct = min_profit_pct

    def check(self, market: Market) -> StructuralArbOpportunity | None:
        if market.no_price <= 0:
            return None

        combined = market.current_price + market.no_price
        profit_pct = 1.0 - combined - self._fee_rate

        if profit_pct < self._min_profit_pct:
            return None

        logger.info(
            "Structural arb: %s YES=%.4f NO=%.4f combined=%.4f profit=%.2f%%",
            market.id[:12], market.current_price, market.no_price, combined, profit_pct * 100,
        )

        return StructuralArbOpportunity(
            market_id=market.id,
            yes_price=market.current_price,
            no_price=market.no_price,
            combined_price=combined,
            expected_profit_pct=profit_pct,
            tokens=market.tokens,
        )
