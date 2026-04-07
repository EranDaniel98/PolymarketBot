"""Phase 4.1 (RMSE-blended sigma) + 4.6 (calibration job) tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_weather.db.models import Base, EdgeCalibration, Opportunity, PolyMarket, Trade
from polymarket_weather.trading.calibration import (
    compute_station_bias,
    run_calibration_job,
)


# ===========================================================================
# 4.1 RMSE-blended sigma
# ===========================================================================

class TestRMSEBlendedSigma:
    def _engine(self):
        from polymarket_weather.weather.forecast import ForecastEngine
        return ForecastEngine(
            metar_only_hours=6, blend_cutoff_hours=30, metar_blend_weight=0.6,
            distribution_df=7, min_confidence=0.5,
            rmse_by_horizon={
                "6h": 1.5, "12h": 2.0, "24h": 2.5, "48h": 3.0,
                "72h": 3.5, "120h": 4.0, "168h": 4.5,
            },
        )

    def test_uses_max_of_spread_and_rmse_when_ensemble_wider(self):
        """If ensemble_std > rmse, blended sigma must use ensemble_std (capped)."""
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=5.0,   # bigger than 24h RMSE of 2.5
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
        )
        assert result is not None
        # final = sqrt(max(25, 6.25) + 0) = 5.0
        assert abs(result.forecast_sigma - 5.0) < 0.01

    def test_uses_max_of_spread_and_rmse_when_rmse_wider(self):
        """If rmse > ensemble_std, blended sigma must be rmse (never trust a
        tight ensemble more than observational history)."""
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=0.5,   # smaller than 24h RMSE of 2.5
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
        )
        assert result is not None
        # final = sqrt(max(0.25, 6.25) + 0) = 2.5
        assert abs(result.forecast_sigma - 2.5) < 0.01

    def test_bias_adds_quadratically(self):
        """A known station bias must inflate sigma via sigma² += bias²."""
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=3.0,
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
            bias=4.0,
        )
        assert result is not None
        # sigma² = max(9, 6.25) + 16 = 9 + 16 = 25 → sigma = 5.0
        assert abs(result.forecast_sigma - 5.0) < 0.01

    def test_fallback_to_rmse_when_ensemble_too_small(self):
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=1.0,  # irrelevant, n_members < 10
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=5,
            bias=0.0,
        )
        assert result is not None
        # Pure RMSE: 2.5
        assert abs(result.forecast_sigma - 2.5) < 0.01

    def test_fallback_to_rmse_when_ensemble_std_none(self):
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=None,
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
            bias=0.0,
        )
        assert result is not None
        assert abs(result.forecast_sigma - 2.5) < 0.01

    def test_fallback_rmse_still_honors_bias(self):
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=None,
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
            bias=3.0,
        )
        assert result is not None
        # sigma = sqrt(2.5² + 3²) = sqrt(6.25 + 9) = sqrt(15.25) ≈ 3.905
        assert abs(result.forecast_sigma - 3.905) < 0.01

    def test_details_include_blend_breakdown(self):
        engine = self._engine()
        result = engine.compute_from_ensemble(
            ensemble_mean=70.0,
            ensemble_std=5.0,
            hours_to_resolution=24,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
            n_members=51,
            bias=1.0,
        )
        assert result is not None
        details = result.details
        assert details["ensemble_std"] == 5.0
        assert details["rmse"] == 2.5
        assert details["bias"] == 1.0
        assert "final_sigma" in details


# ===========================================================================
# 4.6 Calibration job
# ===========================================================================

@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _insert_market(sf):
    async with sf() as session:
        m = PolyMarket(
            market_id="0xmkt",
            question="Will it be warm?",
            yes_token_id="yes",
            no_token_id="no",
            status="active",
        )
        session.add(m)
        await session.commit()


async def _insert_opportunity_and_trade(
    sf, *, our_p, direction, settlement_result, settled_at=None, source="nwp_ensemble",
) -> int:
    """Helper: create one Opportunity + one settled Trade tied to it."""
    async with sf() as session:
        opp = Opportunity(
            market_id="0xmkt",
            our_p=our_p,
            market_p=0.5,
            edge=0.1,
            direction=direction,
            confidence=0.85,
            forecast_source=source,
        )
        session.add(opp)
        await session.commit()
        await session.refresh(opp)

        trade = Trade(
            opportunity_id=opp.id,
            market_id="0xmkt",
            direction=direction,
            status="settled",
            size_usdc=10.0,
            fill_price=0.5,
            settlement_result=settlement_result,
            settled_at=settled_at or datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.commit()
        return opp.id


class TestCalibrationJob:
    @pytest.mark.asyncio
    async def test_no_trades_returns_zero(self, session_factory):
        written = await run_calibration_job(session_factory)
        assert written == 0

    @pytest.mark.asyncio
    async def test_writes_row_for_settled_trade(self, session_factory):
        await _insert_market(session_factory)
        opp_id = await _insert_opportunity_and_trade(
            session_factory,
            our_p=0.75, direction="YES", settlement_result="YES",
        )
        written = await run_calibration_job(session_factory)
        assert written == 1
        # Verify the row
        async with session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(EdgeCalibration))
            rows = result.scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.opportunity_id == opp_id
            assert float(row.our_p) == 0.75
            assert row.actual_outcome is True
            assert row.forecast_source == "nwp_ensemble"

    @pytest.mark.asyncio
    async def test_idempotent_no_double_writes(self, session_factory):
        await _insert_market(session_factory)
        await _insert_opportunity_and_trade(
            session_factory,
            our_p=0.75, direction="YES", settlement_result="YES",
        )
        first = await run_calibration_job(session_factory)
        second = await run_calibration_job(session_factory)
        assert first == 1
        assert second == 0  # Already recorded, skipped

    @pytest.mark.asyncio
    async def test_actual_outcome_flips_for_NO_direction_YES_settlement(self, session_factory):
        """If you bet NO and the market settles YES, you lost (actual_outcome=False)."""
        await _insert_market(session_factory)
        await _insert_opportunity_and_trade(
            session_factory,
            our_p=0.30, direction="NO", settlement_result="YES",
        )
        await run_calibration_job(session_factory)
        async with session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(EdgeCalibration))
            row = result.scalars().first()
            assert row.actual_outcome is False  # Bet NO, got YES → lost

    @pytest.mark.asyncio
    async def test_skips_unsettled_trades(self, session_factory):
        await _insert_market(session_factory)
        # Insert an opportunity + an OPEN trade
        async with session_factory() as session:
            opp = Opportunity(
                market_id="0xmkt", our_p=0.5, market_p=0.4, edge=0.1,
                direction="YES", confidence=0.9, forecast_source="nwp_ensemble",
            )
            session.add(opp)
            await session.commit()
            await session.refresh(opp)
            session.add(Trade(
                opportunity_id=opp.id, market_id="0xmkt", direction="YES",
                status="open", size_usdc=10.0, fill_price=0.5,
            ))
            await session.commit()

        written = await run_calibration_job(session_factory)
        assert written == 0

    @pytest.mark.asyncio
    async def test_respects_lookback_window(self, session_factory):
        await _insert_market(session_factory)
        old = datetime.now(timezone.utc) - timedelta(days=200)
        await _insert_opportunity_and_trade(
            session_factory,
            our_p=0.75, direction="YES", settlement_result="YES",
            settled_at=old,
        )
        written = await run_calibration_job(session_factory, lookback_days=90)
        assert written == 0


class TestStationBias:
    @pytest.mark.asyncio
    async def test_bias_none_when_insufficient_samples(self, session_factory):
        bias = await compute_station_bias(session_factory, "KJFK", min_samples=30)
        assert bias is None

    @pytest.mark.asyncio
    async def test_bias_computed_from_calibration_rows(self, session_factory):
        """Insert 30 calibration rows where our_p=0.8 but only half the
        outcomes are True → actual rate ≈ 0.5 → bias ≈ 0.3 (over-confident)."""
        async with session_factory() as session:
            for i in range(30):
                session.add(EdgeCalibration(
                    our_p=0.8,
                    actual_outcome=(i % 2 == 0),  # 15 True, 15 False
                    forecast_source="nwp_ensemble",
                    station_id="KJFK",
                    resolved_at=datetime.now(timezone.utc),
                ))
            await session.commit()

        bias = await compute_station_bias(session_factory, "KJFK", min_samples=30)
        assert bias is not None
        # mean(our_p - outcome) = mean(0.8 - 1, 0.8 - 0, ...) = 0.8 - 0.5 = 0.3
        assert abs(bias - 0.3) < 0.01
