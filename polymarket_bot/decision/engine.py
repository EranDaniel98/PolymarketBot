import logging
import math
from datetime import datetime, timezone

from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.database import Database
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import (
    ArbitrageOpportunity, Direction, Market, OrderType, Signal,
    SignalEvent, TradeDecision,
)

logger = logging.getLogger(__name__)


def _clamp(x: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, x))


class DecisionEngine:
    def __init__(
        self,
        risk_manager: RiskManager,
        event_bus: EventBus,
        database: Database,
        thresholds: ConfidenceThresholds,
        signals_config: SignalsConfig,
    ):
        self._risk = risk_manager
        self._bus = event_bus
        self._db = database
        self._thresholds = thresholds
        self._weights = {
            "news": signals_config.news.weight,
            "social": signals_config.social.weight,
            "polls": signals_config.polls.weight,
            "llm": signals_config.llm.weight,
            "bookmaker": signals_config.bookmaker.weight,
            "favorite_longshot": signals_config.favorite_longshot.weight,
            "divergence": signals_config.divergence.weight,
            "weather": signals_config.weather.weight,
            "whale": signals_config.whale.weight,
        }

    def _freshness_factor(self, signal: Signal) -> float:
        """Exponential decay based on signal age (half-life = 2 hours)."""
        age_minutes = (datetime.now(timezone.utc) - signal.timestamp).total_seconds() / 60
        return math.exp(-age_minutes / 120)

    def aggregate_signals(self, signals: list[Signal]) -> float:
        """Aggregate signals using log-odds for proper probability combination.

        Returns a composite confidence in [0, 1]. Higher = stronger agreement
        in the majority direction.
        """
        if not signals:
            return 0.0

        # Use log-odds aggregation: convert each signal's confidence to
        # log-odds, weight them, then convert back. This properly handles
        # combining evidence from multiple sources without the linear
        # averaging bugs (e.g. NO signals being inverted).
        yes_log_odds = 0.0
        no_log_odds = 0.0

        for signal in signals:
            weight = self._weights.get(signal.source, 0.1) * self._freshness_factor(signal)
            # Convert confidence to log-odds contribution
            c = _clamp(signal.confidence)
            log_odds = math.log(c / (1 - c)) * weight
            if signal.direction == Direction.YES:
                yes_log_odds += log_odds
            else:
                no_log_odds += log_odds

        # The composite is the strength of the majority direction
        net_log_odds = abs(yes_log_odds - no_log_odds)
        composite = 1.0 / (1.0 + math.exp(-net_log_odds))

        # Consensus discount: only when 3+ signals agree AND total sources > 3
        # (correlated signals shouldn't stack confidence linearly)
        majority_dir = Direction.YES if yes_log_odds >= no_log_odds else Direction.NO
        agreeing_count = sum(1 for s in signals if s.direction == majority_dir)
        total_sources = len({s.source for s in signals})
        if agreeing_count >= 3 and total_sources <= agreeing_count:
            composite *= 0.90
            logger.info("Consensus discount: %d/%d sources agree", agreeing_count, total_sources)

        return composite

    def determine_majority_direction(self, signals: list[Signal]) -> Direction:
        yes_weight = 0.0
        no_weight = 0.0
        for signal in signals:
            w = self._weights.get(signal.source, 0.1) * self._freshness_factor(signal)
            c = _clamp(signal.confidence)
            log_odds = math.log(c / (1 - c)) * w
            if signal.direction == Direction.YES:
                yes_weight += log_odds
            else:
                no_weight += log_odds
        return Direction.YES if yes_weight >= no_weight else Direction.NO

    def determine_action(self, composite_confidence: float) -> str:
        if composite_confidence >= self._thresholds.auto_execute:
            return "auto_execute"
        elif composite_confidence >= self._thresholds.notify:
            return "notify"
        return "log_only"

    async def on_signal(self, signal_event: SignalEvent) -> None:
        if self._risk.circuit_breaker_active:
            logger.warning("Circuit breaker active — ignoring signal")
            return

        signal = signal_event.signal
        market = signal_event.market
        await self._db.save_signal(signal)
        await self._db.save_signal_outcome(
            source=signal.source,
            market_id=signal.market_id,
            predicted_direction=signal.direction.value,
            confidence=signal.confidence,
            market_price=market.current_price,
            timestamp=signal.timestamp,
        )

        recent_rows = await self._db.get_signals(market.id)
        recent_signals = [signal]
        for row in recent_rows:
            try:
                recent_signals.append(Signal(
                    source=row["source"],
                    market_id=row["market_id"],
                    direction=Direction(row["direction"]),
                    confidence=row["confidence"],
                    reasoning=row.get("reasoning", ""),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                ))
            except (KeyError, ValueError):
                continue
        # Deduplicate by source — keep most recent per source
        seen_sources: dict[str, Signal] = {}
        for s in recent_signals:
            if s.source not in seen_sources or s.timestamp > seen_sources[s.source].timestamp:
                seen_sources[s.source] = s
        recent_signals = list(seen_sources.values())

        composite = self.aggregate_signals(recent_signals)
        action = self.determine_action(composite)

        # Multi-signal gate: require 2+ distinct sources for auto_execute
        distinct_sources = {s.source for s in recent_signals}
        if action == "auto_execute" and len(distinct_sources) < self._thresholds.min_signal_sources:
            logger.warning(
                "Downgraded auto_execute->notify for %s: only %d source(s) (%s)",
                market.id, len(distinct_sources), ", ".join(distinct_sources),
            )
            action = "notify"

        if action == "log_only":
            logger.info("Low confidence %.2f for %s — logging only", composite, market.id)
            return

        direction = self.determine_majority_direction(recent_signals)
        size = await self._risk.calculate_position_size(composite, market.current_price)

        decision = TradeDecision(
            market_id=market.id,
            direction=direction,
            amount=size,
            confidence=composite,
            signals=recent_signals,
            order_type=OrderType.LIMIT,
            tokens=market.tokens,
        )

        approved, reason = await self._risk.check(decision, market.current_price)
        if not approved:
            logger.info("Risk rejected: %s", reason)
            return

        if action == "auto_execute":
            await self._bus.publish("trade_decision", decision)
        elif action == "notify":
            await self._bus.publish("approval_request", decision)

    async def on_arb_opportunity(self, arb: ArbitrageOpportunity) -> None:
        if self._risk.circuit_breaker_active:
            return

        polymarket_id = arb.market_ids.get("polymarket")
        if not polymarket_id:
            return

        polymarket_price = arb.prices.get("polymarket", 0)
        avg_other = sum(
            p for k, p in arb.prices.items() if k != "polymarket"
        ) / max(len(arb.prices) - 1, 1)

        direction = Direction.YES if avg_other > polymarket_price else Direction.NO
        size = await self._risk.calculate_position_size(arb.confidence, polymarket_price)

        decision = TradeDecision(
            market_id=polymarket_id,
            direction=direction,
            amount=size,
            confidence=arb.confidence,
            signals=[],
            order_type=OrderType.MARKET if arb.time_sensitivity == "high" else OrderType.LIMIT,
            arb_opportunity=arb,
        )

        approved, reason = await self._risk.check(decision, polymarket_price)
        if not approved:
            logger.info("Arb risk rejected: %s", reason)
            return

        await self._bus.publish("trade_decision", decision)
