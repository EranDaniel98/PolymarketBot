"""Forecast engine: converts weather data into calibrated probabilities.

Three regimes based on hours to resolution:
  1. < metar_only_hours: METAR trend extrapolation
  2. metar_only_hours .. blend_cutoff_hours: METAR + NWP blend
  3. > blend_cutoff_hours: NWP ensemble only

All probability calculations use Student's t-distribution for better
tail behaviour than the normal distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.stats import t as t_dist


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class ForecastResult:
    probability: float        # P(condition met), 0-1
    confidence: float         # 0-1, based on data quality and CI width
    source: str               # "metar" | "metar_nwp" | "nwp_ensemble"
    data_age_minutes: float   # Age of freshest data point
    forecast_mean: float      # Predicted temperature (Celsius)
    forecast_sigma: float     # Uncertainty (Celsius)
    details: dict             # Full data for audit snapshot


# ---------------------------------------------------------------------------
# Probability helpers (Student's t)
# ---------------------------------------------------------------------------

def compute_probability_above(forecast_mean: float, sigma: float,
                              threshold: float, df: int = 7) -> float:
    """P(temp > threshold) using Student's t-distribution.

    All values in the same unit (Celsius or Fahrenheit).
    """
    if sigma <= 0:
        return 1.0 if forecast_mean > threshold else 0.0
    z = (threshold - forecast_mean) / sigma
    return float(1.0 - t_dist.cdf(z, df))


def compute_probability_below(forecast_mean: float, sigma: float,
                              threshold: float, df: int = 7) -> float:
    """P(temp < threshold) using Student's t-distribution."""
    if sigma <= 0:
        return 1.0 if forecast_mean < threshold else 0.0
    z = (threshold - forecast_mean) / sigma
    return float(t_dist.cdf(z, df))


def compute_probability_range(forecast_mean: float, sigma: float,
                              lower: float, upper: float, df: int = 7) -> float:
    """P(lower <= temp <= upper) using Student's t-distribution.

    For Polymarket's 2-degree bucket markets.
    """
    if sigma <= 0:
        return 1.0 if lower <= forecast_mean <= upper else 0.0
    z_lower = (lower - forecast_mean) / sigma
    z_upper = (upper - forecast_mean) / sigma
    return float(t_dist.cdf(z_upper, df) - t_dist.cdf(z_lower, df))


# ---------------------------------------------------------------------------
# METAR trend extrapolation
# ---------------------------------------------------------------------------

def metar_trend_forecast(readings: list[tuple[datetime, float]],
                         target: datetime) -> tuple[float, float]:
    """Fit linear regression to recent METAR readings and extrapolate.

    Args:
        readings: List of (timestamp, temperature_celsius) tuples, sorted by time.
        target: Target datetime to forecast.

    Returns:
        (predicted_mean, estimated_sigma)

    Raises:
        ValueError: If fewer than 2 readings provided.
    """
    if len(readings) < 2:
        raise ValueError("Need at least 2 readings for trend extrapolation")

    # Convert to relative hours from first reading
    t0 = readings[0][0]
    x = np.array([(r[0] - t0).total_seconds() / 3600.0 for r in readings])
    y = np.array([r[1] for r in readings])

    # Linear regression: y = slope * x + intercept
    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs

    # Predict at target time
    target_x = (target - t0).total_seconds() / 3600.0
    predicted = slope * target_x + intercept

    # Sigma: combine regression residual std with extrapolation uncertainty
    residuals = y - (slope * x + intercept)
    residual_std = float(np.std(residuals, ddof=1)) if len(readings) > 2 else 1.0

    # Extrapolation uncertainty grows with distance from data
    max_x = float(np.max(x))
    extrapolation_distance = max(0, target_x - max_x)
    # Add ~0.5 deg-C per hour of extrapolation beyond data
    extrapolation_penalty = extrapolation_distance * 0.5

    sigma = max(residual_std + extrapolation_penalty, 0.5)  # Floor at 0.5 deg-C

    return float(predicted), sigma


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

def fahrenheit_to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9


def celsius_to_fahrenheit(c: float) -> float:
    return c * 9 / 5 + 32


# ---------------------------------------------------------------------------
# RMSE lookup
# ---------------------------------------------------------------------------

def get_rmse_for_horizon(hours: float, rmse_table: dict[str, float]) -> float:
    """Get interpolated RMSE for a given forecast horizon.

    rmse_table: {"6h": 1.5, "12h": 2.0, "24h": 2.5, ...}
    """
    # Parse table into sorted (hours, rmse) pairs
    points: list[tuple[float, float]] = []
    for key, val in rmse_table.items():
        h = float(key.replace("h", ""))
        points.append((h, val))
    points.sort()

    if not points:
        return 3.0  # Fallback

    # Clamp to table bounds
    if hours <= points[0][0]:
        return points[0][1]
    if hours >= points[-1][0]:
        return points[-1][1]

    # Linear interpolation
    for i in range(len(points) - 1):
        h1, r1 = points[i]
        h2, r2 = points[i + 1]
        if h1 <= hours <= h2:
            frac = (hours - h1) / (h2 - h1)
            return r1 + frac * (r2 - r1)

    return points[-1][1]


# ---------------------------------------------------------------------------
# ForecastEngine
# ---------------------------------------------------------------------------

class ForecastEngine:
    """Computes P(condition met) for weather markets."""

    def __init__(
        self,
        metar_only_hours: float,
        blend_cutoff_hours: float,
        metar_blend_weight: float,
        distribution_df: int,
        min_confidence: float,
        rmse_by_horizon: dict[str, float],
    ) -> None:
        self._metar_only_hours = metar_only_hours
        self._blend_cutoff_hours = blend_cutoff_hours
        self._metar_blend_weight = metar_blend_weight
        self._df = distribution_df
        self._min_confidence = min_confidence
        self._rmse_table = rmse_by_horizon

    # -- METAR-only regime (< metar_only_hours) ---------------------------

    def compute_from_metar(
        self,
        readings: list[tuple[datetime, float]],
        target: datetime,
        threshold: float,
        threshold_upper: float | None,
        direction: str,
    ) -> ForecastResult | None:
        """Compute probability from METAR trend alone (< 6h regime)."""
        if len(readings) < 2:
            return None

        mean, sigma = metar_trend_forecast(readings, target)
        prob = self._compute_prob(mean, sigma, threshold, threshold_upper, direction)

        # Confidence based on data freshness and number of readings
        latest_reading_time = max(r[0] for r in readings)
        age_minutes = (
            (datetime.now(target.tzinfo or None) - latest_reading_time).total_seconds()
            / 60
        )
        confidence = min(0.95, 0.5 + len(readings) * 0.05) * max(
            0, 1 - age_minutes / 180
        )

        return ForecastResult(
            probability=prob,
            confidence=confidence,
            source="metar",
            data_age_minutes=age_minutes,
            forecast_mean=mean,
            forecast_sigma=sigma,
            details={
                "n_readings": len(readings),
                "slope_per_hour": float(mean - readings[-1][1]),
            },
        )

    # -- NWP ensemble regime (> blend_cutoff_hours) ------------------------

    def compute_from_ensemble(
        self,
        ensemble_mean: float,
        ensemble_std: float,
        hours_to_resolution: float,
        threshold: float,
        threshold_upper: float | None,
        direction: str,
        n_members: int = 51,
    ) -> ForecastResult | None:
        """Compute probability from NWP ensemble (30h+ regime)."""
        # Use ensemble spread if enough members, else fall back to RMSE table
        if n_members >= 10 and ensemble_std > 0:
            sigma = ensemble_std
        else:
            sigma = get_rmse_for_horizon(hours_to_resolution, self._rmse_table)

        prob = self._compute_prob(
            ensemble_mean, sigma, threshold, threshold_upper, direction
        )

        # Confidence degrades with horizon
        base_confidence = min(0.95, 0.4 + n_members * 0.01)
        horizon_penalty = min(0.3, hours_to_resolution / 500)
        confidence = max(0.1, base_confidence - horizon_penalty)

        return ForecastResult(
            probability=prob,
            confidence=confidence,
            source="nwp_ensemble",
            data_age_minutes=0,
            forecast_mean=ensemble_mean,
            forecast_sigma=sigma,
            details={
                "n_members": n_members,
                "hours_to_resolution": hours_to_resolution,
            },
        )

    # -- Blended regime (metar_only_hours .. blend_cutoff_hours) -----------

    def compute_blended(
        self,
        metar_readings: list[tuple[datetime, float]],
        ensemble_mean: float,
        ensemble_std: float,
        target: datetime,
        hours_to_resolution: float,
        threshold: float,
        threshold_upper: float | None,
        direction: str,
        n_members: int = 51,
    ) -> ForecastResult | None:
        """Compute blended METAR + NWP probability (6-30h regime).

        Weight ramps linearly:
        - At metar_only_hours: metar_blend_weight (default 0.6) for METAR
        - At blend_cutoff_hours: 0.3 for METAR, 0.7 for NWP
        """
        metar_result = self.compute_from_metar(
            metar_readings, target, threshold, threshold_upper, direction
        )
        nwp_result = self.compute_from_ensemble(
            ensemble_mean,
            ensemble_std,
            hours_to_resolution,
            threshold,
            threshold_upper,
            direction,
            n_members,
        )

        if metar_result is None and nwp_result is None:
            return None
        if metar_result is None:
            return nwp_result
        if nwp_result is None:
            return metar_result

        # Linear weight ramp
        blend_range = self._blend_cutoff_hours - self._metar_only_hours
        if blend_range <= 0:
            metar_weight = 0.5
        else:
            progress = (hours_to_resolution - self._metar_only_hours) / blend_range
            progress = max(0.0, min(1.0, progress))
            # METAR weight decreases from metar_blend_weight to 0.3
            metar_weight = self._metar_blend_weight - progress * (
                self._metar_blend_weight - 0.3
            )

        nwp_weight = 1.0 - metar_weight
        blended_prob = (
            metar_weight * metar_result.probability
            + nwp_weight * nwp_result.probability
        )
        blended_confidence = (
            metar_weight * metar_result.confidence
            + nwp_weight * nwp_result.confidence
        )
        blended_mean = (
            metar_weight * metar_result.forecast_mean
            + nwp_weight * nwp_result.forecast_mean
        )
        blended_sigma = (
            metar_weight * metar_result.forecast_sigma ** 2
            + nwp_weight * nwp_result.forecast_sigma ** 2
        ) ** 0.5

        return ForecastResult(
            probability=blended_prob,
            confidence=blended_confidence,
            source="metar_nwp",
            data_age_minutes=metar_result.data_age_minutes,
            forecast_mean=blended_mean,
            forecast_sigma=blended_sigma,
            details={
                "metar_weight": metar_weight,
                "nwp_weight": nwp_weight,
                "metar_prob": metar_result.probability,
                "nwp_prob": nwp_result.probability,
            },
        )

    # -- Internal ---------------------------------------------------------

    def _compute_prob(
        self,
        mean: float,
        sigma: float,
        threshold: float,
        threshold_upper: float | None,
        direction: str,
    ) -> float:
        if direction == "range" and threshold_upper is not None:
            return compute_probability_range(
                mean, sigma, threshold, threshold_upper, self._df
            )
        elif direction == "above":
            return compute_probability_above(mean, sigma, threshold, self._df)
        elif direction == "below":
            return compute_probability_below(mean, sigma, threshold, self._df)
        else:
            return 0.5  # Unknown direction
