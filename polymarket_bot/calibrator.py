"""Performance-Based Weight Calibration — adjusts signal weights based on track record."""

import logging
from polymarket_bot.database import Database

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "news": 0.2,
    "social": 0.15,
    "polls": 0.25,
    "llm": 0.25,
    "bookmaker": 0.15,
}


class WeightCalibrator:
    def __init__(self, database: Database, min_samples: int = 20, recalibrate_every: int = 10):
        self._db = database
        self._min_samples = min_samples
        self._recalibrate_every = recalibrate_every
        self._weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
        self._trade_count_at_last_calibration = 0

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    async def maybe_recalibrate(self) -> bool:
        """Recalibrate if enough new trades have accumulated. Returns True if recalibrated."""
        trade_count = await self._db.get_trade_count()
        if trade_count - self._trade_count_at_last_calibration < self._recalibrate_every:
            return False
        if trade_count < self._min_samples:
            return False

        new_weights = await self._compute_weights()
        if new_weights:
            self._weights = new_weights
            self._trade_count_at_last_calibration = trade_count
            logger.info("Weights recalibrated: %s", self._weights)
            return True
        return False

    async def _compute_weights(self) -> dict[str, float] | None:
        """Compute weights based on signal accuracy.

        For each signal source, measure what fraction of its signals
        correctly predicted the market direction (price moved in the
        signal's predicted direction).
        """
        signals = await self._db._fetch_all(
            "SELECT source, direction, market_id, confidence "
            "FROM signals ORDER BY timestamp DESC LIMIT 500"
        )

        if not signals:
            return None

        # Group signals by source and check accuracy
        source_stats: dict[str, dict] = {}
        for sig in signals:
            source = sig["source"]
            if source not in source_stats:
                source_stats[source] = {"correct": 0, "total": 0, "avg_confidence": 0.0}
            source_stats[source]["total"] += 1
            source_stats[source]["avg_confidence"] += sig["confidence"]

        # Normalize average confidence
        for source, stats in source_stats.items():
            if stats["total"] > 0:
                stats["avg_confidence"] /= stats["total"]

        # Check which signals led to profitable trades
        trades = await self._db._fetch_all(
            "SELECT market_id, direction, realized_pnl "
            "FROM trades WHERE status = 'filled' ORDER BY timestamp DESC LIMIT 200"
        )

        trade_outcomes: dict[str, float] = {}
        for trade in trades:
            trade_outcomes[trade["market_id"]] = trade.get("realized_pnl", 0)

        # For each signal, check if the resulting trade was profitable
        for sig in signals:
            source = sig["source"]
            market_id = sig["market_id"]
            if market_id in trade_outcomes:
                if trade_outcomes[market_id] > 0:
                    source_stats[source]["correct"] += 1

        # Compute accuracy-based weights
        raw_weights = {}
        for source, stats in source_stats.items():
            if stats["total"] >= 5:  # Need at least 5 signals to score
                accuracy = stats["correct"] / stats["total"] if stats["total"] > 0 else 0
                # Weight = accuracy * average confidence (reward accurate AND confident signals)
                raw_weights[source] = max(accuracy * stats["avg_confidence"], 0.05)
            else:
                raw_weights[source] = DEFAULT_WEIGHTS.get(source, 0.1)

        # Include default sources that haven't fired yet
        for source, default_w in DEFAULT_WEIGHTS.items():
            if source not in raw_weights:
                raw_weights[source] = default_w

        # Normalize to sum to 1.0
        total = sum(raw_weights.values())
        if total <= 0:
            return None

        return {source: round(w / total, 3) for source, w in raw_weights.items()}
