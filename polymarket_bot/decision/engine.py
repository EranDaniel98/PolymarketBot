import logging
import math
from datetime import datetime, timezone

from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.database import Database
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import (
    ArbitrageOpportunity, Direction, Market, OrderType, Signal,
    SignalBatchEvent, SignalEvent, TradeDecision,
)

logger = logging.getLogger(__name__)


def _clamp(x: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, x))


def _infer_category(question: str) -> str:
    """Infer category from question text when Gamma API doesn't provide one."""
    q = question.lower()
    if any(w in q for w in ("election", "president", "congress", "vote", "poll")):
        return "politics"
    if any(w in q for w in ("bitcoin", "ethereum", "crypto", "btc", "eth")):
        return "crypto"
    if any(w in q for w in ("nba", "nfl", "mlb", "game", "match", "score")):
        return "sports"
    if any(w in q for w in ("temperature", "weather", "hurricane", "storm")):
        return "weather"
    return "general"


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

    # Signal half-lives in minutes
    SIGNAL_HALF_LIVES = {
        "favorite_longshot": 1440,  # 24 hours — price structure changes slowly
        "divergence": 1440,         # 24 hours — platform gaps persist
        "weather": 720,             # 12 hours — forecasts update twice daily
        "polls": 720,               # 12 hours
        "llm": 360,                 # 6 hours
        "bookmaker": 180,           # 3 hours — odds move faster
        "whale": 60,                # 1 hour — whale impact fades quickly
        "news": 60,                 # 1 hour — news cycles fast
    }

    def _freshness_factor(self, signal: Signal) -> float:
        """Exponential decay based on signal age with per-source half-life."""
        age_minutes = (datetime.now(timezone.utc) - signal.timestamp).total_seconds() / 60
        half_life = self.SIGNAL_HALF_LIVES.get(signal.source, 120)
        return math.exp(-age_minutes / half_life)

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

        # Correlation-aware consensus: only discount if agreeing sources share data
        CORRELATED_PAIRS = {
            frozenset({"news", "social"}),
            frozenset({"news", "llm"}),    # LLM reads news headlines
            frozenset({"social", "llm"}),  # LLM reads Reddit
        }
        majority_dir = Direction.YES if yes_log_odds >= no_log_odds else Direction.NO
        agreeing_sources = {s.source for s in signals if s.direction == majority_dir}
        correlated_count = sum(
            1 for pair in CORRELATED_PAIRS
            if pair.issubset(agreeing_sources)
        )
        if correlated_count > 0:
            discount = 0.95 ** correlated_count  # 5% per correlated pair
            composite *= discount
            logger.info("Correlation discount: %.0f%% (%d correlated pairs in %s)",
                       discount * 100, correlated_count, agreeing_sources)

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

    def set_exit_manager(self, exit_manager) -> None:
        """Set exit manager reference for open position checks."""
        self._exit_manager = exit_manager

    def set_market_cache(self, cache: dict) -> None:
        """Set shared market cache for question/category lookups."""
        self._market_cache = cache

    async def _try_rotation(
        self, new_decision: TradeDecision, new_market_price: float, _slog
    ) -> bool:
        """Try to rotate out the weakest position to make room for a better trade.

        Returns True if rotation was initiated.
        """
        if not hasattr(self, '_exit_manager') or not self._exit_manager:
            return False

        from polymarket_bot.decision.risk import estimate_true_probability

        # Compute new trade's edge
        p_est = estimate_true_probability(new_decision.confidence, new_market_price)
        new_edge = abs(p_est - new_market_price)

        # Find the weakest position eligible for rotation
        price_getter = self._exit_manager._price_getter
        candidate = self._risk.find_rotation_candidate(new_edge, price_getter)
        if candidate is None:
            return False

        rotate_id, worst_edge = candidate
        pos = self._exit_manager._positions[rotate_id]

        _slog.info(
            "Rotation: selling %s (edge %.3f) for %s (edge %.3f)",
            rotate_id[:16], worst_edge, new_decision.market_id[:16], new_edge,
            extra={
                "event_type": "position_rotation",
                "exit_market_id": rotate_id,
                "exit_edge": round(worst_edge, 4),
                "enter_market_id": new_decision.market_id,
                "enter_edge": round(new_edge, 4),
                "enter_confidence": new_decision.confidence,
            },
        )

        # Trigger exit on the weak position
        exit_decision = TradeDecision(
            market_id=pos.market_id,
            direction=pos.direction,
            amount=pos.amount,
            confidence=0.99,
            signals=[],
            order_type=OrderType.LIMIT,
            tokens=pos.tokens,
            is_exit=True,
        )
        await self._bus.publish("trade_decision", exit_decision)
        await self._exit_manager.track_exit(pos.market_id)
        self._risk.record_exit(pos.market_id)

        # Now publish the new trade
        await self._bus.publish("trade_decision", new_decision)
        return True

    async def _exposure_ratio(self) -> float:
        """Return current exposure as a fraction of the limit (0.0 to 1.0+)."""
        exposure = await self._db.get_total_exposure()
        max_exposure = self._risk._bankroll * self._risk._config.max_exposure_pct
        return exposure / max_exposure if max_exposure > 0 else 1.0

    async def on_signal(self, signal_event: SignalEvent) -> None:
        if self._risk.circuit_breaker_active:
            logger.warning("Circuit breaker active — ignoring signal")
            return

        # Skip markets where we already hold a position
        if hasattr(self, '_exit_manager') and self._exit_manager:
            if signal_event.market.id in self._exit_manager._positions:
                logger.debug("Already holding position in %s — skipping", signal_event.market.id)
                return

        # Short-circuit when exposure is maxed and no rotation possible
        exposure_pct = await self._exposure_ratio()
        if exposure_pct >= self._risk._config.rotation_exposure_threshold:
            # Check if rotation might be possible before doing expensive work
            if not (hasattr(self, '_exit_manager') and self._exit_manager
                    and self._exit_manager._positions):
                logger.debug("Exposure at %.0f%% with no positions to rotate — skipping %s",
                            exposure_pct * 100, signal_event.market.id)
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

        await self._make_decision(market, recent_signals)

    async def _make_decision(self, market: Market, recent_signals: list[Signal]) -> None:
        """Core decision logic: aggregate signals, check risk, publish trade.

        Called by both on_signal() and on_signal_batch() after their respective
        signal-gathering and deduplication steps.
        """
        composite = self.aggregate_signals(recent_signals)
        action = self.determine_action(composite)

        # Auto-approve mode: skip Telegram approval, execute all notify-level trades
        if action == "notify" and self._thresholds.auto_approve_all:
            logger.info("Auto-approve mode: promoting notify->auto_execute for %s", market.id)
            action = "auto_execute"

        # Multi-signal gate: require 2+ distinct sources for auto_execute
        # Applied AFTER auto-approve to prevent single-source trades from executing
        distinct_sources = {s.source for s in recent_signals}
        if action == "auto_execute" and len(distinct_sources) < self._thresholds.min_signal_sources:
            logger.warning(
                "Downgraded auto_execute->notify for %s: only %d source(s) (%s)",
                market.id, len(distinct_sources), ", ".join(distinct_sources),
            )
            action = "notify"

        # Structured log for log_only decisions (missed opportunities)
        import logging as _logging
        _slog = _logging.getLogger("polymarket_bot.structured")

        if action == "log_only":
            _slog.info(
                "Skipped: %.2f for %s", composite, market.id[:16],
                extra={
                    "event_type": "trade_skipped",
                    "market_id": market.id,
                    "question": market.question,
                    "category": market.category or "",
                    "confidence": composite,
                    "market_price": market.current_price,
                    "volume": market.volume,
                    "signals": [
                        {"source": s.source, "direction": s.direction.value,
                         "confidence": round(s.confidence, 3)}
                        for s in recent_signals
                    ],
                    "distinct_sources": len({s.source for s in recent_signals}),
                },
            )
            return

        direction = self.determine_majority_direction(recent_signals)
        size = await self._risk.calculate_position_size(composite, market.current_price)

        _slog.info(
            "Decision: %s %s $%.2f (%s)", direction.value, market.id[:16], size, action,
            extra={
                "event_type": "trade_decision",
                "market_id": market.id,
                "question": market.question,
                "category": market.category or "",
                "direction": direction.value,
                "amount": size,
                "confidence": composite,
                "action": action,
                "market_price": market.current_price,
                "volume": market.volume,
                "signals": [
                    {"source": s.source, "direction": s.direction.value,
                     "confidence": round(s.confidence, 3),
                     "reasoning": s.reasoning[:500]}
                    for s in recent_signals
                ],
                "distinct_sources": len({s.source for s in recent_signals}),
            },
        )

        decision = TradeDecision(
            market_id=market.id,
            direction=direction,
            amount=size,
            confidence=composite,
            signals=recent_signals,
            order_type=OrderType.LIMIT,
            tokens=market.tokens,
            question=market.question,
            category=market.category or _infer_category(market.question),
        )

        approved, reason = await self._risk.check(decision, market.current_price)
        if not approved:
            # Try position rotation if rejected due to max exposure
            rotated = False
            if "Max exposure" in reason:
                rotated = await self._try_rotation(decision, market.current_price, _slog)

            if not rotated:
                _slog.warning(
                    "Risk rejected: %s %s — %s", direction.value, market.id[:16], reason,
                    extra={
                        "event_type": "risk_rejected",
                        "market_id": market.id,
                        "question": market.question,
                        "direction": direction.value,
                        "amount": size,
                        "confidence": composite,
                        "market_price": market.current_price,
                        "rejection_reason": reason,
                        "signals": [s.source for s in recent_signals],
                    },
                )
            # Rotation already published both exit and entry — done
            return

        # Save signal-to-trade linkage
        trade_id = f"{market.id}_{direction.value}_{datetime.now(timezone.utc).isoformat()}"
        await self._db.save_trade_signals(trade_id, recent_signals)

        if action == "auto_execute":
            await self._bus.publish("trade_decision", decision)
        elif action == "notify":
            await self._bus.publish("approval_request", decision)

    async def on_signal_batch(self, batch: SignalBatchEvent) -> None:
        """Process a batch of signals from a single evaluation cycle for one market.

        All signals are saved to DB first, then merged with prior-cycle DB signals
        from other sources, deduplicated, and fed into _make_decision().
        """
        if self._risk.circuit_breaker_active:
            logger.warning("Circuit breaker active — ignoring signal batch")
            return

        market = batch.market

        # Skip markets where we already hold a position
        if hasattr(self, '_exit_manager') and self._exit_manager:
            if market.id in self._exit_manager._positions:
                logger.debug("Already holding position in %s — skipping batch", market.id)
                return

        # Short-circuit when exposure is maxed and no rotation possible
        exposure_pct = await self._exposure_ratio()
        if exposure_pct >= self._risk._config.rotation_exposure_threshold:
            if not (hasattr(self, '_exit_manager') and self._exit_manager
                    and self._exit_manager._positions):
                logger.debug("Exposure at %.0f%% with no positions to rotate — skipping batch %s",
                             exposure_pct * 100, market.id)
                return

        # Persist every signal in the batch
        for signal in batch.signals:
            await self._db.save_signal(signal)

        # Collect the batch signals as the authoritative set for their sources
        batch_sources = {s.source for s in batch.signals}
        all_signals: list[Signal] = list(batch.signals)

        # Merge with prior-cycle DB signals from *other* sources to avoid duplicates
        recent_rows = await self._db.get_signals(market.id)
        for row in recent_rows:
            if row["source"] in batch_sources:
                continue  # already have fresher version from batch
            try:
                all_signals.append(Signal(
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
        for s in all_signals:
            if s.source not in seen_sources or s.timestamp > seen_sources[s.source].timestamp:
                seen_sources[s.source] = s
        recent_signals = list(seen_sources.values())

        await self._make_decision(market, recent_signals)

    async def on_arb_opportunity(self, arb: ArbitrageOpportunity) -> None:
        if self._risk.circuit_breaker_active:
            return

        exposure_pct = await self._exposure_ratio()
        if exposure_pct >= 1.0:
            # Arb path doesn't do rotation — too time-sensitive
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

        # Look up market for category inference
        cached = self._market_cache.get(polymarket_id) if hasattr(self, '_market_cache') else None
        question = cached.question if cached else ""
        category = (cached.category if cached else "") or _infer_category(question)

        decision = TradeDecision(
            market_id=polymarket_id,
            direction=direction,
            amount=size,
            confidence=arb.confidence,
            signals=[],
            order_type=OrderType.MARKET if arb.time_sensitivity == "high" else OrderType.LIMIT,
            arb_opportunity=arb,
            question=question,
            category=category,
        )

        approved, reason = await self._risk.check(decision, polymarket_price)
        if not approved:
            logger.info("Arb risk rejected: %s", reason)
            return

        await self._bus.publish("trade_decision", decision)
