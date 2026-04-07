"""Calibration job — compares predicted probabilities against realized outcomes.

Phase 4.6. Runs daily. For every settled trade in the last 90 days, records
one EdgeCalibration row mapping (forecast_source, our_p) → actual_outcome.
Downstream: /api/calibration reads these rows, bins them by our_p, and
reports observed_rate per bin, which the dashboard renders as the
reliability diagram.

Also computes per-station bias = mean(our_p − observed_outcome_p) across
recent resolutions. The bias is a scalar adjustment the ForecastEngine
can consume via the `bias` parameter of compute_from_ensemble (Phase 4.1),
correcting systematic offsets like 'KJFK always runs 0.6 cold in winter'.

This module is intentionally stand-alone and has no concept of in-memory
state — every call reads the trades table, writes EdgeCalibration rows,
commits, and returns. Safe to call repeatedly (idempotent via
(opportunity_id) uniqueness if the caller wants to dedupe).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from polymarket_weather.db.models import EdgeCalibration, Opportunity, Trade

logger = logging.getLogger(__name__)


async def run_calibration_job(
    session_factory: async_sessionmaker,
    *,
    lookback_days: int = 90,
) -> int:
    """Walk recently-settled trades and write their outcomes to edge_calibration.

    Returns the number of new calibration rows written. Idempotent: skips
    any trade that already has a calibration row keyed on opportunity_id.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    async with session_factory() as session:
        # Fetch settled trades joined with their originating opportunity so we
        # can get our_p, edge, forecast_source, and the YES/NO resolution.
        result = await session.execute(
            sa.select(Trade, Opportunity)
            .join(Opportunity, Trade.opportunity_id == Opportunity.id, isouter=True)
            .where(Trade.status == "settled")
            .where(Trade.settled_at >= cutoff)
        )
        rows = result.all()

        # Pre-load existing calibration rows so we can skip duplicates.
        existing_q = await session.execute(
            sa.select(EdgeCalibration.opportunity_id).where(
                EdgeCalibration.opportunity_id.isnot(None)
            )
        )
        existing_ids: set[int] = {row[0] for row in existing_q.all()}

        new_rows = 0
        for trade, opp in rows:
            if opp is None or opp.id in existing_ids:
                continue
            if trade.settlement_result not in ("YES", "NO"):
                continue
            actual_outcome = trade.settlement_result == opp.direction
            calibration = EdgeCalibration(
                opportunity_id=opp.id,
                our_p=float(opp.our_p),
                actual_outcome=actual_outcome,
                forecast_source=opp.forecast_source or "unknown",
                resolved_at=trade.settled_at,
                station_id=None,  # denormalized lookup not wired yet
                hours_to_resolution=None,
                month=trade.settled_at.month if trade.settled_at else None,
                edge_at_entry=float(opp.edge) if opp.edge is not None else None,
            )
            session.add(calibration)
            new_rows += 1

        if new_rows > 0:
            await session.commit()

    logger.info("Calibration job: wrote %d new rows", new_rows)
    return new_rows


async def compute_station_bias(
    session_factory: async_sessionmaker,
    station_id: str,
    *,
    min_samples: int = 30,
    lookback_days: int = 90,
) -> float | None:
    """Return the station-specific bias in our_p.

    Bias > 0 means our_p was higher than actual — the model is optimistic
    for this station and the ForecastEngine should shift mean down.
    Bias < 0 means we're pessimistic.

    Returns None if we have fewer than `min_samples` resolved calibrations.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    async with session_factory() as session:
        result = await session.execute(
            sa.select(EdgeCalibration.our_p, EdgeCalibration.actual_outcome)
            .where(EdgeCalibration.station_id == station_id)
            .where(EdgeCalibration.resolved_at >= cutoff)
            .where(EdgeCalibration.actual_outcome.isnot(None))
        )
        samples = result.all()

    if len(samples) < min_samples:
        return None

    # Brier-style residual: (our_p - outcome), averaged.
    total = 0.0
    for our_p, outcome in samples:
        outcome_val = 1.0 if outcome else 0.0
        total += float(our_p) - outcome_val
    return total / len(samples)
