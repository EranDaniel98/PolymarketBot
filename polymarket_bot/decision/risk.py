import logging
import math
from datetime import datetime, timedelta, timezone

from polymarket_bot.config import FeeConfig, KellyTier, RiskConfig
from polymarket_bot.database import Database
from polymarket_bot.models import TradeDecision

logger = logging.getLogger(__name__)

# Default fee categories when no FeeConfig provided (updated March 30, 2026)
_CATEGORY_FEES: dict[str, float] = {
    "geopolitics": 0.0,
    "politics": 0.01,
    "sports": 0.0075,
    "crypto": 0.018,
    "finance": 0.012,
    "culture": 0.01,
    "weather": 0.01,
}
_DEFAULT_TAKER_FEE = 0.01


def calculate_round_trip_fee(
    category: str = "",
    is_maker: bool = False,
    fee_config: FeeConfig | None = None,
) -> float:
    """Calculate round-trip fee based on market category and order type.

    Maker orders: 0% fee (limit orders resting on book).
    Taker orders: category-dependent, 0% for geopolitics, up to 3.15% for crypto 5-min.
    """
    if is_maker:
        return fee_config.maker_fee if fee_config else 0.0
    if fee_config and fee_config.category_taker_fees:
        return fee_config.category_taker_fees.get(
            category.lower(), fee_config.default_taker_fee
        )
    return _CATEGORY_FEES.get(category.lower(), _DEFAULT_TAKER_FEE)


def half_kelly(
    p: float, market_price: float, fraction: float = 0.5, round_trip_fee: float = 0.01,
) -> float:
    """Kelly criterion for binary outcome markets, with fee adjustment."""
    if market_price <= 0 or market_price >= 1 or p <= 0 or p >= 1:
        return 0.0
    b = (1 - market_price) / market_price * (1 - round_trip_fee)
    if b <= 0:
        return 0.0
    full_kelly = (p * b - (1 - p)) / b
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
    # Max shift is 25% of the remaining room toward certainty.
    max_shift = 0.25
    room = 1.0 - market_price  # room to move up for YES
    shift = max_shift * room * confidence
    p_estimated = market_price + shift
    return max(0.01, min(0.99, p_estimated))


class RiskManager:
    def __init__(self, config: RiskConfig, database: Database, bankroll: float,
                 exit_manager=None, fee_config: FeeConfig | None = None):
        self._config = config
        self._db = database
        self._bankroll = bankroll
        self._circuit_breaker_active = False
        self._recovery_until: datetime | None = None
        self._cooldowns: dict[str, datetime] = {}
        self._exit_manager = exit_manager
        self._fee_config = fee_config

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    @property
    def in_recovery(self) -> bool:
        """True if circuit breaker was recently reset and we're in reduced-sizing recovery."""
        if self._recovery_until is None:
            return False
        if datetime.now(timezone.utc) >= self._recovery_until:
            self._recovery_until = None
            return False
        return True

    def _kelly_fraction_for_confidence(self, confidence: float) -> float:
        """Return Kelly fraction based on confidence tier."""
        if self._config.kelly_tiers:
            for tier in sorted(self._config.kelly_tiers, key=lambda t: t.max_confidence):
                if confidence <= tier.max_confidence:
                    return tier.fraction
            return self._config.kelly_fraction
        # Tiers calibrated for post-discount composite confidence
        if confidence < 0.52:
            return 0.25
        elif confidence <= 0.62:
            return 0.35
        else:
            return self._config.kelly_fraction

    async def calculate_position_size(
        self, confidence: float, market_price: float, category: str = "",
    ) -> float:
        trade_count = await self._db.get_trade_count()

        bootstrap_pct = self._config.bootstrap_size_pct
        bootstrap_limit = self._config.bootstrap_trades

        # Use category-aware fee for Kelly calculation (prefer maker/limit orders)
        fee = calculate_round_trip_fee(category, is_maker=True, fee_config=self._fee_config)

        if trade_count >= bootstrap_limit:
            # Full Kelly sizing with confidence-tiered fraction
            kelly_frac = self._kelly_fraction_for_confidence(confidence)
            p_estimated = estimate_true_probability(confidence, market_price)
            fraction = half_kelly(p_estimated, market_price, kelly_frac, round_trip_fee=fee)
            size = self._bankroll * fraction
        elif trade_count >= bootstrap_limit // 2:
            # Smooth transition: blend bootstrap and Kelly
            kelly_frac = self._kelly_fraction_for_confidence(confidence)
            blend = (trade_count - bootstrap_limit // 2) / (bootstrap_limit // 2)
            bootstrap_size = self._bankroll * bootstrap_pct
            p_estimated = estimate_true_probability(confidence, market_price)
            fraction = half_kelly(p_estimated, market_price, kelly_frac, round_trip_fee=fee)
            kelly_size = self._bankroll * fraction
            size = bootstrap_size * (1 - blend) + kelly_size * blend
        else:
            # Pure bootstrap sizing
            size = self._bankroll * bootstrap_pct

        max_position = self._bankroll * self._config.max_position_pct
        size = min(size, max_position)

        # Reduce sizing during circuit breaker recovery period
        if self.in_recovery:
            size *= self._config.recovery_sizing_pct
            logger.info("Recovery mode: sizing reduced to %.0f%%", self._config.recovery_sizing_pct * 100)

        return size

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

        # Minimum trade size — ensures fees don't dominate
        if decision.amount < self._config.min_trade_size:
            return False, f"Below minimum trade size: ${decision.amount:.2f} < ${self._config.min_trade_size:.2f}"

        # Correlated exposure check
        if self._exit_manager and hasattr(decision, 'category') and decision.category:
            correlated = self._exit_manager.get_correlated_exposure(decision.category)
            max_correlated = self._bankroll * self._config.max_correlated_exposure_pct
            if correlated + decision.amount > max_correlated:
                return False, (f"Correlated exposure: ${correlated:.2f} + ${decision.amount:.2f} "
                              f"exceeds ${max_correlated:.2f} for category '{decision.category}'")

        # Min edge — check estimated probability vs market, accounting for fees
        p_est = estimate_true_probability(decision.confidence, market_price)
        raw_edge = abs(p_est - market_price)
        category = getattr(decision, 'category', '') or ''
        fee = calculate_round_trip_fee(category, is_maker=False, fee_config=self._fee_config)
        edge = raw_edge - fee  # Net edge after taker fee
        if edge < self._config.min_edge:
            return False, f"Insufficient edge: {raw_edge:.1%} - {fee:.1%} fee = {edge:.1%} < {self._config.min_edge:.1%}"

        # Cooldown
        last_exit = self._cooldowns.get(decision.market_id)
        if last_exit:
            elapsed = (datetime.now(timezone.utc) - last_exit).total_seconds()
            if elapsed < self._config.cooldown_seconds:
                remaining = self._config.cooldown_seconds - elapsed
                return False, f"Cooldown: {remaining:.0f}s remaining for market {decision.market_id}"

        kelly_frac = self._kelly_fraction_for_confidence(decision.confidence)
        logger.info(
            "Risk: %s — Approved", decision.market_id[:16],
            extra={
                "event_type": "risk_check",
                "market_id": decision.market_id,
                "approved": True,
                "reason": "Approved",
                "amount": decision.amount,
                "raw_edge": round(raw_edge, 4),
                "fee": round(fee, 4),
                "net_edge": round(edge, 4),
                "estimated_probability": round(p_est, 4),
                "market_price": market_price,
                "kelly_fraction": kelly_frac,
                "bankroll": self._bankroll,
                "in_recovery": self.in_recovery,
            },
        )
        return True, "Approved"

    def record_exit(self, market_id: str) -> None:
        self._cooldowns[market_id] = datetime.now(timezone.utc)

    async def maybe_reset_circuit_breaker(self) -> bool:
        """Reset circuit breaker if daily PnL has recovered to above half the trigger threshold."""
        if not self._circuit_breaker_active:
            return False
        daily_pnl = await self._db.get_daily_pnl()
        recovery_threshold = -(self._bankroll * self._config.max_daily_loss_pct / 2)
        if daily_pnl >= recovery_threshold:
            self._circuit_breaker_active = False
            self._recovery_until = datetime.now(timezone.utc) + timedelta(
                hours=self._config.recovery_hours
            )
            logger.info("Circuit breaker reset: daily PnL $%.2f recovered above $%.2f "
                       "(reduced sizing for %dh)",
                       daily_pnl, recovery_threshold, self._config.recovery_hours)
            return True
        return False

    def find_rotation_candidate(
        self, new_edge: float, price_getter
    ) -> tuple[str, float] | None:
        """Find the weakest position to rotate out if the new trade has enough edge advantage.

        Returns (market_id, current_edge) of the candidate, or None.
        """
        if not self._exit_manager:
            return None

        now = datetime.now(timezone.utc)
        min_hold = timedelta(minutes=self._config.rotation_min_hold_minutes)
        multiplier = self._config.rotation_edge_multiplier

        worst_id = None
        worst_edge = float("inf")

        for mid, pos in self._exit_manager._positions.items():
            # Skip positions held less than minimum time
            if now - pos.entry_time < min_hold:
                continue

            current_price = price_getter("polymarket", mid) if price_getter else None
            if current_price is None:
                continue

            # Current edge = how far price has moved in our favor
            if pos.direction.value == "YES":
                edge = current_price - pos.entry_price
            else:
                edge = pos.entry_price - current_price

            if edge < worst_edge:
                worst_edge = edge
                worst_id = mid

        if worst_id is None:
            return None

        # Only rotate if new trade's edge is significantly better
        # For negative-edge positions, always allow rotation if new edge is positive
        if worst_edge < 0 and new_edge > 0:
            return worst_id, worst_edge
        if worst_edge >= 0 and new_edge < multiplier * worst_edge:
            return None

        return worst_id, worst_edge

    def update_bankroll(self, new_bankroll: float) -> None:
        self._bankroll = new_bankroll
