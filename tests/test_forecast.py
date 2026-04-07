"""Tests for the forecast engine (t-distribution probability computation)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_weather.weather.forecast import (
    ForecastEngine,
    ForecastResult,
    celsius_to_fahrenheit,
    compute_probability_above,
    compute_probability_below,
    compute_probability_range,
    fahrenheit_to_celsius,
    get_rmse_for_horizon,
    metar_trend_forecast,
)


# --- Probability computation ---


def test_probability_above_center():
    """Forecast exactly at threshold -> ~50%."""
    p = compute_probability_above(80.0, 3.0, 80.0, df=7)
    assert abs(p - 0.5) < 0.01


def test_probability_above_high():
    """Forecast well above threshold -> high probability."""
    p = compute_probability_above(85.0, 3.0, 78.0, df=7)
    assert p > 0.8


def test_probability_above_low():
    """Forecast well below threshold -> low probability."""
    p = compute_probability_above(70.0, 3.0, 80.0, df=7)
    assert p < 0.05


def test_probability_below():
    p = compute_probability_below(70.0, 3.0, 80.0, df=7)
    assert p > 0.95


def test_probability_range_centered():
    """Forecast at range center -> meaningful probability."""
    p = compute_probability_range(52.0, 3.0, 50.0, 54.0, df=7)
    assert 0.2 < p < 0.6


def test_probability_range_far():
    """Range far from forecast -> near zero."""
    p = compute_probability_range(80.0, 3.0, 50.0, 54.0, df=7)
    assert p < 0.01


def test_probability_zero_sigma_above():
    p = compute_probability_above(85.0, 0.0, 80.0, df=7)
    assert p == 1.0


def test_probability_zero_sigma_below():
    p = compute_probability_above(75.0, 0.0, 80.0, df=7)
    assert p == 0.0


def test_probability_zero_sigma_range():
    p = compute_probability_range(52.0, 0.0, 50.0, 54.0, df=7)
    assert p == 1.0
    p2 = compute_probability_range(49.0, 0.0, 50.0, 54.0, df=7)
    assert p2 == 0.0


# --- METAR trend ---


def test_metar_trend_rising():
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    readings = [
        (now - timedelta(hours=3), 10.0),
        (now - timedelta(hours=2), 11.0),
        (now - timedelta(hours=1), 12.0),
        (now, 13.0),
    ]
    target = now + timedelta(hours=2)
    mean, sigma = metar_trend_forecast(readings, target)
    assert 14.0 < mean < 16.0
    assert sigma > 0


def test_metar_trend_falling():
    now = datetime(2026, 4, 6, 20, 0, tzinfo=timezone.utc)
    readings = [
        (now - timedelta(hours=3), 25.0),
        (now - timedelta(hours=2), 24.0),
        (now - timedelta(hours=1), 23.0),
        (now, 22.0),
    ]
    target = now + timedelta(hours=1)
    mean, sigma = metar_trend_forecast(readings, target)
    assert mean < 22.0


def test_metar_trend_minimum_readings():
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    readings = [
        (now - timedelta(hours=1), 15.0),
        (now, 16.0),
    ]
    target = now + timedelta(hours=1)
    mean, sigma = metar_trend_forecast(readings, target)
    assert mean > 16.0


def test_metar_trend_too_few_readings():
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        metar_trend_forecast([(now, 15.0)], now + timedelta(hours=1))


# --- Unit conversion ---


def test_f_to_c():
    assert abs(fahrenheit_to_celsius(32.0)) < 0.01
    assert abs(fahrenheit_to_celsius(212.0) - 100.0) < 0.01


def test_c_to_f():
    assert abs(celsius_to_fahrenheit(0.0) - 32.0) < 0.01
    assert abs(celsius_to_fahrenheit(100.0) - 212.0) < 0.01


# --- RMSE lookup ---


def test_rmse_interpolation():
    table = {"6h": 1.5, "24h": 2.5, "48h": 3.0}
    assert abs(get_rmse_for_horizon(6, table) - 1.5) < 0.01
    assert abs(get_rmse_for_horizon(24, table) - 2.5) < 0.01
    # 15h should interpolate between 6h and 24h
    rmse_15 = get_rmse_for_horizon(15, table)
    assert 1.5 < rmse_15 < 2.5


def test_rmse_clamp():
    table = {"6h": 1.5, "48h": 3.0}
    assert get_rmse_for_horizon(1, table) == 1.5   # Below min
    assert get_rmse_for_horizon(100, table) == 3.0  # Above max


# --- ForecastEngine ---


def test_engine_metar_above():
    engine = ForecastEngine(
        metar_only_hours=6,
        blend_cutoff_hours=30,
        metar_blend_weight=0.6,
        distribution_df=7,
        min_confidence=0.70,
        rmse_by_horizon={"6h": 1.5, "24h": 2.5},
    )
    now = datetime.now(timezone.utc)
    readings = [
        (now - timedelta(hours=2), 26.0),
        (now - timedelta(hours=1), 27.0),
        (now, 28.0),
    ]
    target = now + timedelta(hours=2)
    result = engine.compute_from_metar(readings, target, 25.0, None, "above")
    assert result is not None
    assert result.probability > 0.5
    assert result.source == "metar"


def test_engine_ensemble_range():
    engine = ForecastEngine(
        metar_only_hours=6,
        blend_cutoff_hours=30,
        metar_blend_weight=0.6,
        distribution_df=7,
        min_confidence=0.70,
        rmse_by_horizon={"6h": 1.5, "48h": 3.0, "168h": 4.5},
    )
    result = engine.compute_from_ensemble(
        ensemble_mean=52.0,
        ensemble_std=2.5,
        hours_to_resolution=48,
        threshold=50.0,
        threshold_upper=54.0,
        direction="range",
        n_members=51,
    )
    assert result is not None
    assert 0.0 < result.probability < 1.0
    assert result.source == "nwp_ensemble"


def test_engine_blended():
    engine = ForecastEngine(
        metar_only_hours=6,
        blend_cutoff_hours=30,
        metar_blend_weight=0.6,
        distribution_df=7,
        min_confidence=0.70,
        rmse_by_horizon={"6h": 1.5, "24h": 2.5},
    )
    now = datetime.now(timezone.utc)
    readings = [
        (now - timedelta(hours=2), 14.0),
        (now - timedelta(hours=1), 15.0),
        (now, 16.0),
    ]
    target = now + timedelta(hours=12)
    result = engine.compute_blended(
        metar_readings=readings,
        ensemble_mean=18.0,
        ensemble_std=2.0,
        target=target,
        hours_to_resolution=12,
        threshold=15.0,
        threshold_upper=None,
        direction="above",
        n_members=51,
    )
    assert result is not None
    assert result.source == "metar_nwp"
    assert result.probability > 0.5
    assert result.details["metar_weight"] > 0
    assert result.details["nwp_weight"] > 0


def test_engine_blended_weight_ramp():
    """At 6h, METAR weight should be ~0.6. At 30h, ~0.3."""
    engine = ForecastEngine(
        metar_only_hours=6,
        blend_cutoff_hours=30,
        metar_blend_weight=0.6,
        distribution_df=7,
        min_confidence=0.70,
        rmse_by_horizon={"6h": 1.5},
    )
    now = datetime.now(timezone.utc)
    readings = [(now - timedelta(hours=1), 20.0), (now, 20.0)]

    # At 6h: metar weight should be ~0.6
    result_6h = engine.compute_blended(
        readings, 20.0, 2.0, now + timedelta(hours=6), 6, 15.0, None, "above", 51
    )
    assert abs(result_6h.details["metar_weight"] - 0.6) < 0.05

    # At 30h: metar weight should be ~0.3
    result_30h = engine.compute_blended(
        readings, 20.0, 2.0, now + timedelta(hours=30), 30, 15.0, None, "above", 51
    )
    assert abs(result_30h.details["metar_weight"] - 0.3) < 0.05


def test_forecast_result_dataclass():
    result = ForecastResult(
        probability=0.72,
        confidence=0.85,
        source="metar",
        data_age_minutes=15.0,
        forecast_mean=28.0,
        forecast_sigma=2.0,
        details={"readings": 4},
    )
    assert result.probability == 0.72
    assert result.source == "metar"
