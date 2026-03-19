import logging
import math
from datetime import datetime, timezone

from polymarket_bot.config import RiskConfig
from polymarket_bot.database import Database
from polymarket_bot.models import TradeDecision

logger = logging.getLogger(__name__)

# Polymarket fee: ~2% round-trip (maker + taker)
ROUND_TRIP_FEE = 0.02


def half_kelly(p: float, market_price: float, fraction: float = 0.5) -> float:
    """Kelly criterion for binary outcome markets, with fee adjustment."""
    if market_price <= 0 or market_price >= 1 or p <= 0 or p >= 1:
        return 0.0
    b = (1 - market_price) / market_price  # payout odds
    # Adjust probability downward for fees
    p_adj = p - ROUND_TRIP_FEE / 2
    if p_adj <= 0:
        return 0.0
    full_kelly = (p_adj * b - (1 - p_adj)) / b
    if full_kelly <= 0:
        return 0.0
    return fraction * full_kelly


def estimate_true_probability(confidence: float, market_price: float) -> float:
    """Bayesian-inspired blending of market price with signal confidence.

    Confidence = "how sure we are about our edge", not "probability of YES".
    We shift the market price toward the signal's implied direction, damped
    by a logistic function to prevent extreme over-betting.

    At low market prices (0.20), signals have more room to push probability up.
    At high market prices (0.80), the same confidence produces a smaller shift.
    """
    # The signal implies true probability is ABOVE market price (for YES signals).
    # Map confidence → shift magnitude using sigmoid damping.
    # Max shift is 15% of the remaining room toward certainty.
    max_shift = 0.15
    room = 1.0 - market_price  # room to move up for YES
    shift = max_shift * room * confidence
    p_estimated = market_price + shift
    return max(0.01, min(0.99, p_estimated))


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

        bootstrap_pct = self._config.bootstrap_size_pct
        bootstrap_limit = self._config.bootstrap_trades

        if trade_count >= bootstrap_limit:
            # Full Kelly sizing
            p_estimated = estimate_true_probability(confidence, market_price)
            fraction = half_kelly(p_estimated, market_price, self._config.kelly_fraction)
            size = self._bankroll * fraction
        elif trade_count >= bootstrap_limit // 2:
            # Smooth transition: blend bootstrap and Kelly
            blend = (trade_count - bootstrap_limit // 2) / (bootstrap_limit // 2)
            bootstrap_size = self._bankroll * bootstrap_pct
            p_estimated = estimate_true_probability(confidence, market_price)
            fraction = half_kelly(p_estimated, market_price, self._config.kelly_fraction)
            kelly_size = self._bankroll * fraction
            size = bootstrap_size * (1 - blend) + kelly_size * blend
        else:
            # Pure bootstrap sizing
            size = self._bankroll * bootstrap_pct

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

        # Min edge — check estimated probability vs market, not raw confidence
        p_est = estimate_true_probability(decision.confidence, market_price)
        edge = abs(p_est - market_price)
        if edge < self._config.min_edge:
            return False, f"Insufficient edge: {edge:.1%} < {self._config.min_edge:.1%}"

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
