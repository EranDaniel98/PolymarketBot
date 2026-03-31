"""Performance-Based Weight Calibration — adjusts signal weights based on track record."""

import logging
from polymarket_bot.database import Database

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "llm": 0.25,
    "favorite_longshot": 0.20,
    "weather": 0.20,
    "crypto_price": 0.20,
    "divergence": 0.15,
    "polls": 0.10,
    "bookmaker": 0.10,
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
        """Compute weights based on signal outcome accuracy."""
        report = await self._db.get_accuracy_report()
        if not report:
            return None

        raw_weights = {}
        for source, stats in report.items():
            if stats["n_signals"] >= 5:
                accuracy = stats["accuracy"]
                avg_conf = stats["avg_confidence"] or 0.5
                raw_weights[source] = max(accuracy * avg_conf, 0.05)
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
