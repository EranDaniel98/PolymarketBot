"""Phase 6 — coverage gap fills.

Targets the specific blind spots the audit agents called out:
  6.1 Forecast regime transitions at exact boundary times (parametric)
  6.2 METAR parser edge cases (-40°C, malformed input, missing T-group)
  6.3 CLOB executor retry path (transient errors → max_retries enforcement)
  6.4 Position manager peak-PnL (run-up then drop)
  6.5 Concurrent risk updates (asyncio.gather of N check_trades)
  6.6 Integration smoke test (mocked transports, full pipeline tick)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ===========================================================================
# 6.1 Forecast regime transitions
# ===========================================================================

class TestForecastRegimeTransitions:
    def _engine(self):
        from polymarket_weather.weather.forecast import ForecastEngine
        return ForecastEngine(
            metar_only_hours=6,
            blend_cutoff_hours=30,
            metar_blend_weight=0.6,
            distribution_df=7,
            min_confidence=0.5,
            rmse_by_horizon={"6h": 1.5, "12h": 2.0, "24h": 2.5, "48h": 3.0, "72h": 3.5},
        )

    def _readings(self, n=5):
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        return [(base + timedelta(minutes=20 * i), 70.0 + i * 0.2) for i in range(n)]

    @pytest.mark.parametrize("hours", [5.99, 6.0, 6.01, 7, 12, 18, 24, 29.99, 30.0, 30.01, 36])
    def test_blend_does_not_crash_at_any_horizon(self, hours):
        """compute_blended must not raise for any reasonable horizon."""
        engine = self._engine()
        target = datetime.now(timezone.utc) + timedelta(hours=hours)
        result = engine.compute_blended(
            metar_readings=self._readings(),
            ensemble_mean=72.0,
            ensemble_std=2.5,
            target=target,
            hours_to_resolution=hours,
            threshold=70.0,
            threshold_upper=None,
            direction="above",
        )
        assert result is None or 0 <= result.probability <= 1

    @pytest.mark.parametrize("hours,expected_metar_w", [
        (6.0, 0.6),    # at metar_only_hours: full METAR weight
        (18.0, 0.45),  # midpoint: average of 0.6 and 0.3
        (30.0, 0.3),   # at blend_cutoff: minimum METAR weight
    ])
    def test_blend_weights_at_boundaries(self, hours, expected_metar_w, monkeypatch):
        """Weight ramp must hit 0.6 at 6h, 0.45 at midpoint, 0.3 at 30h."""
        engine = self._engine()

        # Capture the blended probability and back-solve for the weight.
        # Set METAR p = 0.8, NWP p = 0.4 → blended = 0.6w + 0.4 → solve for w
        target = datetime.now(timezone.utc) + timedelta(hours=hours)

        from polymarket_weather.weather.forecast import ForecastResult
        fake_metar = ForecastResult(
            probability=0.8, confidence=0.9, source="metar",
            data_age_minutes=10, forecast_mean=70.0, forecast_sigma=1.0, details={},
        )
        fake_nwp = ForecastResult(
            probability=0.4, confidence=0.85, source="nwp_ensemble",
            data_age_minutes=0, forecast_mean=70.0, forecast_sigma=2.5, details={},
        )
        monkeypatch.setattr(engine, "compute_from_metar", lambda *a, **k: fake_metar)
        monkeypatch.setattr(engine, "compute_from_ensemble", lambda *a, **k: fake_nwp)

        result = engine.compute_blended(
            metar_readings=self._readings(),
            ensemble_mean=70.0, ensemble_std=2.5,
            target=target, hours_to_resolution=hours,
            threshold=70.0, threshold_upper=None, direction="above",
        )
        assert result is not None
        # blended = 0.8*w + 0.4*(1-w) = 0.4 + 0.4w → w = (blended-0.4)/0.4
        recovered_w = (result.probability - 0.4) / 0.4
        assert abs(recovered_w - expected_metar_w) < 0.01, (
            f"At hours={hours}: expected metar_weight={expected_metar_w}, "
            f"got {recovered_w} (probability={result.probability})"
        )

    def test_blend_handles_missing_metar(self, monkeypatch):
        """If METAR returns None, fall back to NWP only."""
        engine = self._engine()
        from polymarket_weather.weather.forecast import ForecastResult
        fake_nwp = ForecastResult(
            probability=0.7, confidence=0.85, source="nwp_ensemble",
            data_age_minutes=0, forecast_mean=70.0, forecast_sigma=2.5, details={},
        )
        monkeypatch.setattr(engine, "compute_from_metar", lambda *a, **k: None)
        monkeypatch.setattr(engine, "compute_from_ensemble", lambda *a, **k: fake_nwp)
        result = engine.compute_blended(
            metar_readings=[], ensemble_mean=70.0, ensemble_std=2.5,
            target=datetime.now(timezone.utc) + timedelta(hours=18),
            hours_to_resolution=18,
            threshold=70.0, threshold_upper=None, direction="above",
        )
        assert result is not None
        assert result.probability == 0.7

    def test_blend_handles_missing_nwp(self, monkeypatch):
        engine = self._engine()
        from polymarket_weather.weather.forecast import ForecastResult
        fake_metar = ForecastResult(
            probability=0.8, confidence=0.9, source="metar",
            data_age_minutes=10, forecast_mean=70.0, forecast_sigma=1.0, details={},
        )
        monkeypatch.setattr(engine, "compute_from_metar", lambda *a, **k: fake_metar)
        monkeypatch.setattr(engine, "compute_from_ensemble", lambda *a, **k: None)
        result = engine.compute_blended(
            metar_readings=[], ensemble_mean=0, ensemble_std=0,
            target=datetime.now(timezone.utc) + timedelta(hours=18),
            hours_to_resolution=18,
            threshold=70.0, threshold_upper=None, direction="above",
        )
        assert result is not None
        assert result.probability == 0.8


# ===========================================================================
# 6.2 METAR parser edge cases
# ===========================================================================

class TestMetarParserEdgeCases:
    def test_t_group_negative_temperature(self):
        from polymarket_weather.weather.collector import parse_remarks_temperature
        # T-group format: T<sign><3 digits>°C * 10 then <sign><3 digits> dewpoint
        # T1400 = -40.0°C (sign=1 means negative)
        result = parse_remarks_temperature("KORD 011200Z ... RMK T14000089")
        assert result == pytest.approx(-40.0, abs=0.1)

    def test_t_group_positive_high_precision(self):
        from polymarket_weather.weather.collector import parse_remarks_temperature
        # T0123 = +12.3°C
        result = parse_remarks_temperature("KORD 011200Z ... RMK T01230089")
        assert result == pytest.approx(12.3, abs=0.05)

    def test_t_group_missing_returns_none(self):
        from polymarket_weather.weather.collector import parse_remarks_temperature
        result = parse_remarks_temperature("KORD 011200Z 18006KT 10SM CLR 24/M02 A3001")
        assert result is None

    def test_t_group_malformed_returns_none(self):
        from polymarket_weather.weather.collector import parse_remarks_temperature
        result = parse_remarks_temperature("KORD ... RMK T9999")  # too short
        assert result is None

    def test_t_group_empty_string(self):
        from polymarket_weather.weather.collector import parse_remarks_temperature
        assert parse_remarks_temperature("") is None

    def test_wdir_vrb_coerced_to_none(self):
        """Real METAR sends 'VRB' for variable wind direction. Must coerce."""
        from polymarket_weather.weather.collector import parse_metar_response
        # Minimal valid record matching the parser's expectations
        readings = parse_metar_response([{
            "icaoId": "KSFO",
            "obsTime": int(datetime.now(timezone.utc).timestamp()),
            "rawOb": "KSFO 011200Z VRB02KT ...",
            "wdir": "VRB",
            "wspd": 2,
            "temp": 15.0,
            "dewp": 10.0,
        }])
        assert len(readings) == 1
        assert readings[0]["wdir"] is None
        assert readings[0]["wspd"] == 2


# ===========================================================================
# 6.3 CLOB executor retry path
# ===========================================================================

class TestExecutorRetry:
    @pytest.mark.asyncio
    async def test_live_execute_retries_on_transient_error(self, monkeypatch):
        from polymarket_weather.trading.executor import TradeExecutor

        executor = TradeExecutor(
            paper_trading=False, paper_balance=0,
            max_slippage=0.02, max_retries=3,
        )
        # Inject a fake CLOB client
        call_count = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            # tick_size, neg_risk, create_and_post_order all go through to_thread
            if fn.__name__ == "create_and_post_order":
                call_count += 1
                if call_count < 3:
                    raise RuntimeError("transient API error")
                return {"orderID": "order_123"}
            if fn.__name__ == "get_tick_size":
                return 0.01
            if fn.__name__ == "get_neg_risk":
                return False
            return None

        monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
        # Skip actual sleep so the test runs fast
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        executor._clob_client = MagicMock()
        executor._clob_client.get_tick_size = MagicMock(__name__="get_tick_size")
        executor._clob_client.get_neg_risk = MagicMock(__name__="get_neg_risk")
        executor._clob_client.create_and_post_order = MagicMock(__name__="create_and_post_order")

        result = await executor._live_execute(
            token_id="0xtoken", side="BUY", amount=10.0, price=0.40,
            order_type="limit",
        )
        assert result.status == "placed"
        assert result.order_id == "order_123"
        assert call_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_live_execute_gives_up_after_max_retries(self, monkeypatch):
        from polymarket_weather.trading.executor import TradeExecutor

        executor = TradeExecutor(
            paper_trading=False, paper_balance=0,
            max_slippage=0.02, max_retries=3,
        )
        call_count = 0

        async def fake_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            if fn.__name__ == "create_and_post_order":
                call_count += 1
                raise RuntimeError("permanent failure")
            if fn.__name__ == "get_tick_size":
                return 0.01
            if fn.__name__ == "get_neg_risk":
                return False
            return None

        monkeypatch.setattr("asyncio.to_thread", fake_to_thread)
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        executor._clob_client = MagicMock()
        executor._clob_client.get_tick_size = MagicMock(__name__="get_tick_size")
        executor._clob_client.get_neg_risk = MagicMock(__name__="get_neg_risk")
        executor._clob_client.create_and_post_order = MagicMock(__name__="create_and_post_order")

        result = await executor._live_execute(
            token_id="0xtoken", side="BUY", amount=10.0, price=0.40,
            order_type="limit",
        )
        assert result.status == "failed"
        assert call_count == 3  # Attempted exactly max_retries times
        assert "permanent failure" in (result.error or "")


# ===========================================================================
# 6.4 Position manager peak-PnL tracking
# ===========================================================================

class TestPeakPnLTracking:
    def test_peak_only_increases(self):
        from polymarket_weather.trading.positions import PositionManager
        pm = PositionManager()
        pm.track_entry("0xa", "YES", 0.50, 25.0, "nyc", "evt_1")

        # +50% (price 0.75)
        pm.update_peak("0xa", 0.75)
        assert pm.get_position("0xa").peak_pnl_pct == pytest.approx(0.5, abs=0.01)

        # Drops to +10% (price 0.55) — peak must NOT decrease
        pm.update_peak("0xa", 0.55)
        assert pm.get_position("0xa").peak_pnl_pct == pytest.approx(0.5, abs=0.01)

        # Drops to -5% (price 0.475) — peak still 0.5
        pm.update_peak("0xa", 0.475)
        assert pm.get_position("0xa").peak_pnl_pct == pytest.approx(0.5, abs=0.01)

        # New high at +60% (price 0.80) — peak updates
        pm.update_peak("0xa", 0.80)
        assert pm.get_position("0xa").peak_pnl_pct == pytest.approx(0.6, abs=0.01)

    def test_peak_for_no_position(self):
        from polymarket_weather.trading.positions import PositionManager
        pm = PositionManager()
        pm.track_entry("0xa", "NO", 0.30, 10.0, "la", "evt_2")

        # NO position: profit when price drops
        pm.update_peak("0xa", 0.20)  # +33% PnL
        peak = pm.get_position("0xa").peak_pnl_pct
        assert peak == pytest.approx(0.333, abs=0.01)

        # Price rises to 0.25 (only +16%), peak stays
        pm.update_peak("0xa", 0.25)
        assert pm.get_position("0xa").peak_pnl_pct == pytest.approx(0.333, abs=0.01)

    def test_peak_unaffected_by_unknown_market(self):
        from polymarket_weather.trading.positions import PositionManager
        pm = PositionManager()
        # No-op on unknown market — must not crash
        pm.update_peak("0xnone", 0.99)


# ===========================================================================
# 6.5 Concurrent risk updates
# ===========================================================================

class TestConcurrentRiskChecks:
    @pytest.mark.asyncio
    async def test_twenty_simultaneous_check_trades_never_exceed_limit(self):
        """asyncio.gather 20 risk checks in parallel, with size that JUST fits.

        Without the trade_lock guarding the read-then-write critical section
        in the actual pipeline, races could approve multiple over-limit trades.
        Here we verify the lock semantics by serializing the check + record
        pair under a single asyncio.Lock — same pattern as pipeline.evaluate.
        """
        from polymarket_weather.trading.risk import RiskManager

        risk = RiskManager(
            max_position_usdc=20.0,
            max_total_exposure_usdc=100.0,
            max_open_positions=20,
            min_trade_size_usdc=5.0,
        )
        lock = asyncio.Lock()
        approved_count = 0

        async def attempt(i):
            nonlocal approved_count
            async with lock:
                check = risk.check_trade(
                    size_usdc=20.0, city=f"city_{i}", region=f"r_{i}",
                    market_id=f"0x{i}",
                )
                if check.approved:
                    risk.record_entry(f"0x{i}", f"city_{i}", f"r_{i}", 20.0)
                    approved_count += 1

        await asyncio.gather(*(attempt(i) for i in range(20)))

        # max_total_exposure 100 / max_position 20 = 5 trades max
        assert approved_count == 5
        assert risk.total_exposure == 100.0


# ===========================================================================
# 6.6 Integration smoke test — full pipeline tick with mocked transports
# ===========================================================================

class TestIntegrationSmokeTest:
    @pytest.mark.asyncio
    async def test_pipeline_tick_with_mocked_transports(self, monkeypatch):
        """Synthetic 'real' market flowing through the entire pipeline.

        Uses an in-memory SQLite for persistence, MagicMocks for HTTP, and
        verifies that the pipeline writes a Trade row with status='open'
        when conditions are favorable.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from polymarket_weather.db.models import Base, Trade
        from polymarket_weather.markets.scanner import ScannedMarket
        from polymarket_weather.trading.pipeline import MismatchPipeline

        # In-memory DB
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        # Mock collaborators
        city_mapper = MagicMock()
        city_mapper.all_aliases.return_value = ["new york city", "new york", "nyc"]
        city_mapper.resolve.return_value = SimpleNamespace(
            city_name="new york city",
            primary_station="KJFK",
            all_stations=["KJFK"],
            region="northeast", country="US",
            lat=40.64, lon=-73.78,
        )

        forecast_engine = MagicMock()
        forecast_engine.compute_from_ensemble.return_value = SimpleNamespace(
            probability=0.78, confidence=0.85, source="nwp_ensemble",
        )

        nwp = MagicMock()
        nwp.fetch_ensemble = AsyncMock(return_value=SimpleNamespace(
            n_members=51,
            at_time=lambda t: (75.0, 2.5),
        ))

        risk = MagicMock()
        risk.get_max_size.return_value = 50.0
        risk.check_trade.return_value = SimpleNamespace(approved=True, reason="")
        risk.record_entry = MagicMock()

        executor = MagicMock()
        executor.get_balance.return_value = 1000.0
        executor.execute_order = AsyncMock(return_value=SimpleNamespace(
            success=True, order_id="paper_xyz", filled_price=0.40, filled_amount=10.0,
        ))

        positions = MagicMock()
        positions.track_entry = MagicMock()

        pipeline = MismatchPipeline(
            city_mapper=city_mapper,
            forecast_engine=forecast_engine,
            metar_collector=MagicMock(),
            nwp_fetcher=nwp,
            risk_manager=risk,
            executor=executor,
            position_manager=positions,
            session_factory=session_factory,
            trade_lock=asyncio.Lock(),
            notifier=None,
            edge_config=SimpleNamespace(
                min_edge=0.05, min_liquidity_usdc=500, min_confidence=0.7,
                min_hours_to_resolution=2, max_hours_to_resolution=168,
                kelly_fraction=0.5,
            ),
            fee_config=SimpleNamespace(
                default_taker_fee=0.01, weather_taker_fee=0.01, maker_fee=0.0,
            ),
            risk_config=SimpleNamespace(
                max_position_usdc=50.0, min_trade_size_usdc=5.0,
            ),
        )

        scanned = ScannedMarket(
            market_id="0xintegration",
            question="Will the high temperature in New York City be above 75 degrees on April 10?",
            event_id="evt_int",
            yes_token_id="yes_token", no_token_id="no_token",
            current_price=0.40, no_price=0.60,
            end_date=datetime.now(timezone.utc) + timedelta(hours=24),
            resolution_source="weather.com", volume=2000,
            slug="integration-test", category="Weather",
        )

        result = await pipeline.evaluate(scanned)

        assert result.decision == "traded", f"got {result.decision} ({result.reason})"
        executor.execute_order.assert_called_once()
        positions.track_entry.assert_called_once()
        risk.record_entry.assert_called_once()

        # Verify Trade row was persisted
        from sqlalchemy import select
        async with session_factory() as session:
            rows = await session.execute(
                select(Trade).where(Trade.status == "open")
            )
            trades = rows.scalars().all()
            assert len(trades) == 1
            assert trades[0].market_id == "0xintegration"
            assert trades[0].direction == "YES"
            assert trades[0].city == "new york city"
            assert trades[0].region == "northeast"

        await engine.dispose()
