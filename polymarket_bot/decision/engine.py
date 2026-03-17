import logging
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
        }

    def aggregate_signals(self, signals: list[Signal]) -> float:
        if not signals:
            return 0.0

        yes_score = 0.0
        no_score = 0.0
        total_weight = 0.0

        for signal in signals:
            weight = self._weights.get(signal.source, 0.1)
            total_weight += weight
            if signal.direction == Direction.YES:
                yes_score += weight * signal.confidence
            else:
                no_score += weight * signal.confidence

        if total_weight == 0:
            return 0.0

        yes_composite = yes_score / total_weight
        no_composite = no_score / total_weight

        if yes_composite >= no_composite:
            return yes_composite
        return 1.0 - no_composite

    def determine_majority_direction(self, signals: list[Signal]) -> Direction:
        yes_weight = 0.0
        no_weight = 0.0
        for signal in signals:
            w = self._weights.get(signal.source, 0.1)
            if signal.direction == Direction.YES:
                yes_weight += w * signal.confidence
            else:
                no_weight += w * signal.confidence
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
