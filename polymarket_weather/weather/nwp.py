"""NWP Ensemble Fetcher — wraps Open-Meteo /v1/ensemble and /v1/forecast."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EnsembleResult:
    """Parsed result from Open-Meteo ensemble API."""

    times: list[datetime]        # Hourly UTC timestamps
    members: list[list[float]]   # members[i] = temperature series for member i
    lat: float = 0.0
    lon: float = 0.0

    @property
    def n_members(self) -> int:
        return len(self.members)

    @property
    def n_hours(self) -> int:
        return len(self.times)

    def mean_at(self, hour_index: int) -> float:
        """Mean temperature across all members at a specific hour."""
        values = [m[hour_index] for m in self.members]
        return float(np.mean(values))

    def std_at(self, hour_index: int) -> float:
        """Std dev across members at a specific hour (sample std, ddof=1)."""
        values = [m[hour_index] for m in self.members]
        return float(np.std(values, ddof=1))

    def at_time(self, target: datetime) -> tuple[float, float]:
        """Return (mean, std) at the closest available hour to *target*.

        Raises ValueError if there is no forecast data.
        """
        if not self.times:
            raise ValueError("No forecast data available")

        # Ensure target is timezone-aware for comparison
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        min_delta = float("inf")
        best_idx = 0
        for i, t in enumerate(self.times):
            delta = abs((t - target).total_seconds())
            if delta < min_delta:
                min_delta = delta
                best_idx = i

        return self.mean_at(best_idx), self.std_at(best_idx)


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def parse_ensemble_response(data: dict) -> EnsembleResult:
    """Parse an Open-Meteo /v1/ensemble JSON response into an EnsembleResult.

    The API returns model-prefixed hourly keys, e.g.:
      ``hourly.temperature_2m_member01``, ``hourly.temperature_2m_member02``, …
    up to member51 for ECMWF ENS.
    """
    hourly = data.get("hourly", {})

    # Parse timestamps — Open-Meteo returns ISO-8601 without trailing Z (UTC).
    raw_times: list[str] = hourly.get("time", [])
    times: list[datetime] = []
    for t in raw_times:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        times.append(dt)

    # Extract member series — any key matching temperature_2m_memberNN.
    members: list[list[float]] = []
    for key, values in sorted(hourly.items()):
        if key.startswith("temperature_2m_member") and isinstance(values, list):
            members.append(values)

    return EnsembleResult(
        times=times,
        members=members,
        lat=data.get("latitude", 0.0),
        lon=data.get("longitude", 0.0),
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class NwpFetcher:
    """Fetches NWP ensemble forecasts from Open-Meteo."""

    def __init__(
        self,
        api_url: str,
        models: list[str],
        deterministic_url: str = "",
        deterministic_models: list[str] | None = None,
    ) -> None:
        self._api_url = api_url                        # e.g. https://api.open-meteo.com/v1/ensemble
        self._models = models                           # e.g. ["ecmwf_ifs025"]
        self._det_url = deterministic_url               # e.g. https://api.open-meteo.com/v1/forecast
        self._det_models: list[str] = deterministic_models or []
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def fetch_ensemble(
        self,
        lat: float,
        lon: float,
        forecast_days: int = 7,
    ) -> EnsembleResult | None:
        """Fetch ensemble forecast from Open-Meteo.

        Returns an EnsembleResult containing all ensemble members, or None on
        failure (network error, non-200 response, parse error).
        """
        if not self._http:
            return None

        params: dict = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "models": ",".join(self._models),
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }
        try:
            resp = await self._http.get(self._api_url, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "Open-Meteo ensemble error %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            return parse_ensemble_response(resp.json())
        except Exception:
            logger.exception("NWP ensemble fetch failed for %.2f,%.2f", lat, lon)
            return None

    async def fetch_deterministic(
        self,
        lat: float,
        lon: float,
        forecast_days: int = 7,
    ) -> dict[str, list[float]] | None:
        """Fetch deterministic forecasts from multiple NWP models.

        Returns ``{model_name: [hourly_temps]}`` or None on failure.
        Intended as a fallback when ensemble data is unavailable.
        """
        if not self._http or not self._det_url or not self._det_models:
            return None

        params: dict = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m",
            "models": ",".join(self._det_models),
            "forecast_days": forecast_days,
            "timezone": "UTC",
        }
        try:
            resp = await self._http.get(self._det_url, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            hourly = data.get("hourly", {})
            result: dict[str, list[float]] = {}
            for key, values in hourly.items():
                if key.startswith("temperature_2m_") and key != "temperature_2m":
                    model_name = key.replace("temperature_2m_", "")
                    result[model_name] = values
            return result if result else None
        except Exception:
            logger.exception("NWP deterministic fetch failed for %.2f,%.2f", lat, lon)
            return None
