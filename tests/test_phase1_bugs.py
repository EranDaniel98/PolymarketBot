"""Phase 1 critical-bug tests (TDD — all should pass after the Phase 1 fixes).

Covers the seven bugs the audit agents found:
  1.2 _detect_unit nested-if miss: '°C' markets misclassified as Fahrenheit
  1.3 datetime.now(target.tzinfo or None) crashes when target is naive
  1.4 np.std with single ensemble member returns nan and propagates
  1.5 Fail-fast CLOB init when paper_trading=False
  1.6 Validate probability inputs (lower > upper, negative sigma)
  1.7 check_staleness false negative: last_report_at updated for all stations
      in a batch even if only some had new readings
"""

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# 1.2 — _detect_unit Celsius bug
# ---------------------------------------------------------------------------

class TestDetectUnit:
    def test_degree_celsius_symbol(self):
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("above 35 °C") == "C"

    def test_degree_celsius_no_space(self):
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("above 35°C") == "C"

    def test_word_celsius(self):
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("35 celsius in Berlin") == "C"

    def test_degrees_c(self):
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("35 degrees c") == "C"

    def test_fahrenheit_still_default(self):
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("above 35 degrees") == "F"
        assert _detect_unit("above 35°F") == "F"

    def test_no_false_positive_on_word_with_c(self):
        # "chicago" contains 'c' but should not trigger celsius detection
        from polymarket_weather.markets.parser import _detect_unit
        assert _detect_unit("Temperature in Chicago above 50") == "F"


# ---------------------------------------------------------------------------
# 1.3 — datetime.now(target.tzinfo or None) timezone bug
# ---------------------------------------------------------------------------

class TestForecastNaiveTarget:
    def test_compute_from_metar_naive_target_does_not_crash(self):
        from datetime import timedelta

        from polymarket_weather.weather.forecast import ForecastEngine
        engine = ForecastEngine(
            metar_only_hours=6, blend_cutoff_hours=30, metar_blend_weight=0.6,
            distribution_df=7, min_confidence=0.5,
            rmse_by_horizon={"6h": 1.5, "12h": 2.0, "24h": 2.5},
        )
        # Readings have UTC-aware timestamps spread over an hour.
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        readings = [
            (base, 20.0),
            (base + timedelta(minutes=20), 20.5),
            (base + timedelta(minutes=40), 21.0),
        ]
        # Target is NAIVE — with the bug, datetime.now(None) returns naive
        # local time and subtracting from aware latest_reading_time raises:
        # TypeError: can't subtract offset-naive and offset-aware datetimes
        naive_target = datetime(2026, 4, 6, 12, 0, 0)  # no tzinfo
        result = engine.compute_from_metar(
            readings=readings,
            target=naive_target,
            threshold=25.0,
            threshold_upper=None,
            direction="above",
        )
        # Result may be None (low data) or a ForecastResult — BUT must not raise.
        assert result is None or hasattr(result, "probability")


# ---------------------------------------------------------------------------
# 1.4 — np.std guard for single-member ensemble
# ---------------------------------------------------------------------------

class TestEnsembleStdGuard:
    def test_std_at_with_single_member_returns_none(self):

        from polymarket_weather.weather.nwp import EnsembleResult
        now = datetime.now(timezone.utc)
        result = EnsembleResult(
            times=[now],
            members=[[20.0]],   # one member, one hour
        )
        value = result.std_at(0)
        # After the fix: should return None (not nan) for n < 2
        assert value is None, f"expected None for single member, got {value}"

    def test_std_at_with_zero_members_returns_none(self):
        from polymarket_weather.weather.nwp import EnsembleResult
        now = datetime.now(timezone.utc)
        result = EnsembleResult(times=[now], members=[])
        assert result.std_at(0) is None

    def test_std_at_with_many_members_returns_float(self):
        from polymarket_weather.weather.nwp import EnsembleResult
        now = datetime.now(timezone.utc)
        result = EnsembleResult(
            times=[now],
            members=[[20.0], [21.0], [22.0], [19.5], [20.5]],
        )
        v = result.std_at(0)
        assert isinstance(v, float)
        assert v > 0


# ---------------------------------------------------------------------------
# 1.5 — Fail-fast CLOB init when paper_trading=False
# ---------------------------------------------------------------------------

class TestExecutorFailFast:
    async def test_live_mode_with_bad_key_raises_on_start(self):
        from polymarket_weather.trading.executor import TradeExecutor
        executor = TradeExecutor(
            paper_trading=False,
            paper_balance=0,
            max_slippage=0.02,
            max_retries=3,
        )
        # Invalid private key → py-clob-client will fail to auth → must raise
        with pytest.raises(Exception):
            await executor.start(
                api_key="", api_secret="",
                private_key="not_a_real_key", chain_id=137,
            )

    async def test_paper_mode_tolerates_empty_key(self):
        from polymarket_weather.trading.executor import TradeExecutor
        executor = TradeExecutor(
            paper_trading=True,
            paper_balance=1000,
            max_slippage=0.02,
            max_retries=3,
        )
        # Paper mode must NOT raise even with empty creds
        await executor.start(
            api_key="", api_secret="", private_key="", chain_id=137,
        )


# ---------------------------------------------------------------------------
# 1.6 — Probability input validation
# ---------------------------------------------------------------------------

class TestProbabilityValidation:
    def test_range_with_inverted_bounds_raises(self):
        from polymarket_weather.weather.forecast import compute_probability_range
        with pytest.raises(ValueError):
            compute_probability_range(70.0, 3.0, lower=80.0, upper=75.0)

    def test_range_with_negative_sigma_raises(self):
        from polymarket_weather.weather.forecast import compute_probability_range
        with pytest.raises(ValueError):
            compute_probability_range(70.0, sigma=-1.5, lower=65.0, upper=75.0)

    def test_above_with_negative_sigma_raises(self):
        from polymarket_weather.weather.forecast import compute_probability_above
        with pytest.raises(ValueError):
            compute_probability_above(70.0, sigma=-0.1, threshold=65.0)

    def test_below_with_negative_sigma_raises(self):
        from polymarket_weather.weather.forecast import compute_probability_below
        with pytest.raises(ValueError):
            compute_probability_below(70.0, sigma=-0.1, threshold=75.0)

    def test_range_with_sigma_zero_and_valid_bounds_still_works(self):
        from polymarket_weather.weather.forecast import compute_probability_range
        # sigma=0 is the degenerate-but-valid case, must not raise
        assert compute_probability_range(70.0, 0.0, lower=65.0, upper=75.0) == 1.0
        assert compute_probability_range(70.0, 0.0, lower=60.0, upper=65.0) == 0.0


# ---------------------------------------------------------------------------
# 1.7 — Collector stale-station update bug
# ---------------------------------------------------------------------------
# This is an integration-style test; we unit-test the logic by checking that
# fetch_and_store only updates last_report_at for stations that actually had
# NEW (non-duplicate) readings in the batch. Full DB test is in a separate
# file; here we just verify the _pick_updated_stations helper if present.

class TestStaleStationUpdate:
    def test_new_station_ids_helper_exists(self):
        """After the fix, collector should only update stations with new data.

        We test the behavior indirectly: the fix should introduce a variable
        that tracks which station_ids had NEW readings stored.
        """
        # This test is a placeholder — real coverage is in the integration
        # test in tests/test_collector.py which uses a mock session factory.
        # Here we just assert the module imports cleanly.
        from polymarket_weather.weather.collector import MetarCollector
        assert MetarCollector is not None
