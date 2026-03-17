import logging
from datetime import datetime, timezone

from polymarket_bot.config import RiskConfig
from polymarket_bot.database import Database
from polymarket_bot.models import TradeDecision

logger = logging.getLogger(__name__)


def half_kelly(p: float, market_price: float, fraction: float = 0.5) -> float:
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1 - market_price) / market_price  # payout odds
    full_kelly = (p * b - (1 - p)) / b
    if full_kelly <= 0:
        return 0.0
    return fraction * full_kelly


class RiskManager:
    def __init__(self, config: RiskConfig, database: Database, bankroll: float):
        self._config = config
        self._db = database
        self._bankroll = bankroll
        self._circuit_breaker_active = False
        self._cooldowns: dict[str, datetime] = {}

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    async def calculate_position_size(self, confidence: float, market_price: float) -> float:
        trade_count = await self._db.get_trade_count()

        if trade_count < self._config.bootstrap_trades:
            size = self._bankroll * self._config.bootstrap_size_pct
        else:
            fraction = half_kelly(confidence, market_price, self._config.kelly_fraction)
            size = self._bankroll * fraction

        max_position = self._bankroll * self._config.max_position_pct
        return min(size, max_position)

    async def check(self, decision: TradeDecision, market_price: float) -> tuple[bool, str]:
        # Circuit breaker
        daily_pnl = await self._db.get_daily_pnl()
        max_loss = self._bankroll * self._config.max_daily_loss_pct
        if daily_pnl < -max_loss:
            self._circuit_breaker_active = True
            return False, f"Circuit breaker: daily loss ${abs(daily_pnl):.2f} exceeds limit ${max_loss:.2f}"

        # Max total exposure
        exposure = await self._db.get_total_exposure()
        max_exposure = self._bankroll * self._config.max_exposure_pct
        if exposure + decision.amount > max_exposure:
            return False, f"Max exposure: current ${exposure:.2f} + ${decision.amount:.2f} exceeds ${max_exposure:.2f}"

        # Max position per market
        max_position = self._bankroll * self._config.max_position_pct
        if decision.amount > max_position:
            return False, f"Max position: ${decision.amount:.2f} exceeds ${max_position:.2f}"

        # Min edge
        if abs(decision.confidence - market_price) < self._config.min_edge:
            return False, f"Insufficient edge: {abs(decision.confidence - market_price):.1%} < {self._config.min_edge:.1%}"

        # Cooldown
        last_exit = self._cooldowns.get(decision.market_id)
        if last_exit:
            elapsed = (datetime.now(timezone.utc) - last_exit).total_seconds()
            if elapsed < self._config.cooldown_seconds:
                remaining = self._config.cooldown_seconds - elapsed
                return False, f"Cooldown: {remaining:.0f}s remaining for market {decision.market_id}"

        return True, "Approved"

    def record_exit(self, market_id: str) -> None:
        self._cooldowns[market_id] = datetime.now(timezone.utc)

    def update_bankroll(self, new_bankroll: float) -> None:
        self._bankroll = new_bankroll
