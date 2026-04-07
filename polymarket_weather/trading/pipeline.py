"""End-to-end mismatch detection pipeline.

Phase 4 — wires the long-standing TODO in app.py's market_scan_and_mismatch_job.
For each scanned market:

  1. Parse the question text into ParsedMarket (city, threshold, direction).
  2. Resolve the city to an ICAO station + lat/lon via the city_mapper.
  3. Compute hours_to_resolution from the market's end_date.
  4. Reject markets outside [min_hours_to_resolution, max_hours_to_resolution].
  5. Reject zero-liquidity / extreme-price markets (Phase 4.5).
  6. Get a forecast appropriate to the horizon (METAR / blend / NWP ensemble).
  7. Reject if forecast confidence is below the configured floor.
  8. Compute our_p, edge, and direction.
  9. Reject if edge below min_edge for the source.
 10. Pick the appropriate market price for the chosen direction (Phase 4.4).
 11. Compute Kelly size with fee adjustment.
 12. Acquire trade_lock, run risk check, place order, persist + record state.
 13. Send Telegram alert.

Failures at any step are logged and the market is skipped — never crash the
loop. The pipeline is deliberately a single async function so the trade_lock
covers the full critical section (risk check → execute → record).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polymarket_weather.db import persistence
from polymarket_weather.markets.parser import ParsedMarket, parse_market_question
from polymarket_weather.markets.scanner import ScannedMarket
from polymarket_weather.trading.mismatch import (
    compute_edge,
    compute_kelly_size,
    get_min_edge_for_source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    market_id: str
    decision: str             # "traded" | "skipped" | "error"
    reason: str = ""          # short reason for skipped/error
    our_p: float | None = None
    market_p: float | None = None
    edge: float | None = None
    direction: str | None = None
    size_usdc: float | None = None


# ---------------------------------------------------------------------------
# Skip reasons (machine-readable strings used by tests + metrics)
# ---------------------------------------------------------------------------

REASON_UNPARSEABLE = "unparseable_question"
REASON_UNKNOWN_CITY = "unknown_city"
REASON_NO_END_DATE = "no_end_date"
REASON_HORIZON_TOO_SHORT = "horizon_too_short"
REASON_HORIZON_TOO_LONG = "horizon_too_long"
REASON_LOW_LIQUIDITY = "low_liquidity"
REASON_EXTREME_PRICE = "extreme_price"
REASON_FORECAST_UNAVAILABLE = "forecast_unavailable"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_INSUFFICIENT_EDGE = "insufficient_edge"
REASON_KELLY_ZERO = "kelly_zero"
REASON_RISK_REJECTED = "risk_rejected"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class MismatchPipeline:
    """Stateless coordinator that ties together scanner output and traders.

    Holds references to all the collaborators it needs but no per-market state.
    Designed for dependency injection so it's trivial to test with mocks.
    """

    def __init__(
        self,
        *,
        city_mapper: Any,
        forecast_engine: Any,
        metar_collector: Any,
        nwp_fetcher: Any,
        risk_manager: Any,
        executor: Any,
        position_manager: Any,
        session_factory: Any,
        trade_lock: Any,
        notifier: Any = None,
        edge_config: Any = None,
        fee_config: Any = None,
        trading_config: Any = None,
        risk_config: Any = None,
    ) -> None:
        self.city_mapper = city_mapper
        self.forecast = forecast_engine
        self.metar = metar_collector
        self.nwp = nwp_fetcher
        self.risk = risk_manager
        self.executor = executor
        self.positions = position_manager
        self.session_factory = session_factory
        self.trade_lock = trade_lock
        self.notifier = notifier
        self.edge_config = edge_config
        self.fee_config = fee_config
        self.trading_config = trading_config
        self.risk_config = risk_config

    async def evaluate(self, scanned: ScannedMarket) -> PipelineResult:
        """Evaluate a single scanned market end-to-end. Never raises."""
        try:
            return await self._evaluate_unsafe(scanned)
        except Exception as exc:
            logger.exception("pipeline.evaluate crashed for %s", scanned.market_id)
            return PipelineResult(
                market_id=scanned.market_id,
                decision="error",
                reason=f"exception: {type(exc).__name__}",
            )

    async def _evaluate_unsafe(self, scanned: ScannedMarket) -> PipelineResult:
        # 1. Parse question
        parsed = parse_market_question(
            scanned.question,
            known_aliases=self.city_mapper.all_aliases(),
        )
        if parsed is None:
            return PipelineResult(scanned.market_id, "skipped", REASON_UNPARSEABLE)

        # 2. Resolve city
        city_match = self.city_mapper.resolve(parsed.city)
        if city_match is None:
            return PipelineResult(scanned.market_id, "skipped", REASON_UNKNOWN_CITY)

        # 3. Hours to resolution
        if scanned.end_date is None:
            return PipelineResult(scanned.market_id, "skipped", REASON_NO_END_DATE)
        end_date = scanned.end_date
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        hours = (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours < (self.edge_config.min_hours_to_resolution if self.edge_config else 2):
            return PipelineResult(scanned.market_id, "skipped", REASON_HORIZON_TOO_SHORT)
        if hours > (self.edge_config.max_hours_to_resolution if self.edge_config else 168):
            return PipelineResult(scanned.market_id, "skipped", REASON_HORIZON_TOO_LONG)

        # 4. Liquidity + extreme-price guards (Phase 4.5)
        min_liq = self.edge_config.min_liquidity_usdc if self.edge_config else 500
        if scanned.volume < min_liq:
            return PipelineResult(scanned.market_id, "skipped", REASON_LOW_LIQUIDITY)
        if scanned.current_price <= 0.02 or scanned.current_price >= 0.98:
            return PipelineResult(scanned.market_id, "skipped", REASON_EXTREME_PRICE)

        # 5. Get forecast (currently NWP for any horizon since METAR is point-in-time)
        forecast_result = await self._get_forecast(parsed, city_match, hours)
        if forecast_result is None:
            return PipelineResult(scanned.market_id, "skipped", REASON_FORECAST_UNAVAILABLE)

        min_conf = self.edge_config.min_confidence if self.edge_config else 0.7
        if forecast_result.confidence < min_conf:
            return PipelineResult(scanned.market_id, "skipped", REASON_LOW_CONFIDENCE)

        # 6. Edge
        our_p = forecast_result.probability
        edge_result = compute_edge(our_p, scanned.current_price)

        min_edge = get_min_edge_for_source(forecast_result.source)
        if self.edge_config and self.edge_config.min_edge:
            min_edge = max(min_edge, self.edge_config.min_edge)
        if edge_result.raw_edge < min_edge:
            return PipelineResult(
                scanned.market_id, "skipped", REASON_INSUFFICIENT_EDGE,
                our_p=our_p, market_p=scanned.current_price,
                edge=edge_result.raw_edge, direction=edge_result.direction,
            )

        # 7. Pick the price the bot would actually PAY for this side (Phase 4.4)
        price_for_side = (
            scanned.current_price if edge_result.direction == "YES" else scanned.no_price
        )

        # 8. Kelly size
        bankroll = self.executor.get_balance() or 0.0
        fee = self._fee_for(scanned.category)
        kelly_frac = self.edge_config.kelly_fraction if self.edge_config else 0.5
        max_pos = self.risk_config.max_position_usdc if self.risk_config else 50.0
        min_pos = self.risk_config.min_trade_size_usdc if self.risk_config else 5.0

        size = compute_kelly_size(
            edge=edge_result.raw_edge,
            market_price=price_for_side,
            direction=edge_result.direction,
            bankroll=bankroll,
            kelly_fraction=kelly_frac,
            fee=fee,
            max_position=max_pos,
            min_position=min_pos,
        )
        if size <= 0:
            return PipelineResult(
                scanned.market_id, "skipped", REASON_KELLY_ZERO,
                our_p=our_p, market_p=scanned.current_price,
                edge=edge_result.raw_edge, direction=edge_result.direction,
            )

        # 9. Critical section — risk check + execute + record under lock
        async with self.trade_lock:
            # Apply bootstrap cap inside the lock so concurrent calls see latest count
            size = min(size, self.risk.get_max_size())
            if size < min_pos:
                return PipelineResult(
                    scanned.market_id, "skipped", REASON_KELLY_ZERO,
                    our_p=our_p, market_p=scanned.current_price,
                    edge=edge_result.raw_edge, direction=edge_result.direction,
                )

            check = self.risk.check_trade(
                size_usdc=size,
                city=city_match.city_name,
                region=city_match.region,
                market_id=scanned.market_id,
            )
            if not check.approved:
                return PipelineResult(
                    scanned.market_id, "skipped",
                    f"{REASON_RISK_REJECTED}:{check.reason}",
                    our_p=our_p, market_p=scanned.current_price,
                    edge=edge_result.raw_edge, direction=edge_result.direction,
                )

            # 10. Place order (paper or live)
            token_id = (
                scanned.yes_token_id if edge_result.direction == "YES" else scanned.no_token_id
            )
            order_result = await self.executor.execute_order(
                token_id=token_id,
                side="BUY",
                amount=size,
                price=price_for_side,
                order_type="limit",
            )
            if not getattr(order_result, "success", True):
                return PipelineResult(
                    scanned.market_id, "skipped",
                    "order_failed",
                    our_p=our_p, market_p=price_for_side,
                    edge=edge_result.raw_edge, direction=edge_result.direction,
                    size_usdc=size,
                )

            # 11. Persist + record in-memory
            try:
                await persistence.persist_position_entry(
                    self.session_factory,
                    persistence.PersistedPosition(
                        market_id=scanned.market_id,
                        direction=edge_result.direction,
                        entry_price=price_for_side,
                        size_usdc=size,
                        city=city_match.city_name,
                        region=city_match.region,
                        event_id=scanned.event_id,
                        entry_time=datetime.now(timezone.utc),
                        peak_pnl_pct=0.0,
                    ),
                )
            except Exception:
                logger.exception("persist_position_entry failed for %s", scanned.market_id)

            self.positions.track_entry(
                market_id=scanned.market_id,
                direction=edge_result.direction,
                entry_price=price_for_side,
                size_usdc=size,
                city=city_match.city_name,
                event_id=scanned.event_id,
            )
            self.risk.record_entry(
                scanned.market_id, city_match.city_name, city_match.region, size,
            )

        # 12. Telegram alert (best-effort, outside lock)
        if self.notifier is not None:
            try:
                await self.notifier.send_trade_placed(
                    market_id=scanned.market_id,
                    direction=edge_result.direction,
                    size=size,
                    price=price_for_side,
                    our_p=our_p,
                    edge=edge_result.raw_edge,
                )
            except Exception:
                logger.exception("notifier.send_trade_placed failed")

        logger.info(
            "[TRADED] %s %s @ $%.4f x $%.2f (our_p=%.3f edge=%.3f city=%s)",
            edge_result.direction, scanned.market_id[:12],
            price_for_side, size, our_p, edge_result.raw_edge, city_match.city_name,
        )
        return PipelineResult(
            market_id=scanned.market_id,
            decision="traded",
            our_p=our_p,
            market_p=price_for_side,
            edge=edge_result.raw_edge,
            direction=edge_result.direction,
            size_usdc=size,
        )

    # -- Helpers ------------------------------------------------------------

    async def _get_forecast(self, parsed: ParsedMarket, city_match: Any, hours: float) -> Any:
        """Pick the right forecast regime based on hours-to-resolution.

        For now we always go through the NWP ensemble path because METAR
        readings need a target time + recent series, which adds another DB
        query. Phase 4 follow-up: blend regimes here.
        """
        try:
            ensemble = await self.nwp.fetch_ensemble(
                lat=city_match.lat, lon=city_match.lon,
            )
            if ensemble is None:
                return None
            target = datetime.now(timezone.utc).replace(microsecond=0)
            # Approximate target = now + hours
            from datetime import timedelta
            target = target + timedelta(hours=hours)
            mean, std = ensemble.at_time(target)
            return self.forecast.compute_from_ensemble(
                ensemble_mean=mean,
                ensemble_std=std,
                hours_to_resolution=hours,
                threshold=parsed.threshold,
                threshold_upper=parsed.threshold_upper,
                direction=parsed.direction,
                n_members=ensemble.n_members,
            )
        except Exception:
            logger.exception("forecast lookup failed for %s", parsed.city)
            return None

    def _fee_for(self, category: str) -> float:
        if self.fee_config is None:
            return 0.01
        return float(self.fee_config.weather_taker_fee or self.fee_config.default_taker_fee)
