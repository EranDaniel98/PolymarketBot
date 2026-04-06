"""METAR collector: fetches, parses, deduplicates, and stores METAR observations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_remarks_temperature(raw_metar: str) -> float | None:
    """Extract precise temperature from METAR remarks T-group.

    Format: T followed by 8 digits. First 4 = temperature, last 4 = dewpoint.
    First digit: 0 = positive, 1 = negative.
    Remaining 3 digits: temperature in tenths of degree C.

    Example: T01560083 → temp = +15.6°C, dewpoint = +8.3°C
    Example: T10221083 → temp = -2.2°C, dewpoint = -8.3°C
    """
    match = re.search(r'\bT(\d{4})(\d{4})\b', raw_metar)
    if not match:
        return None
    temp_raw = match.group(1)
    sign = -1 if temp_raw[0] == '1' else 1
    value = int(temp_raw[1:]) / 10.0
    return sign * value


def parse_metar_response(json_data: list[dict]) -> list[dict]:
    """Parse aviationweather.gov METAR JSON response into dicts ready for DB storage."""
    readings = []
    for obs in json_data:
        station_id = obs.get("icaoId")
        obs_time = obs.get("obsTime")
        if not station_id or obs_time is None:
            continue

        raw_metar = obs.get("rawOb", "")
        temp_precise = parse_remarks_temperature(raw_metar) if raw_metar else None

        readings.append({
            "station_id": station_id,
            "observed_at": datetime.fromtimestamp(obs_time, tz=timezone.utc),
            "temp": obs.get("temp"),
            "dewp": obs.get("dewp"),
            "wdir": obs.get("wdir"),
            "wspd": obs.get("wspd"),
            "wgst": obs.get("wgst"),
            "altim": obs.get("altim"),
            "slp": obs.get("slp"),
            "visib": str(obs.get("visib", "")) if obs.get("visib") is not None else None,
            "cloud_cover": obs.get("clouds"),
            "wx_string": obs.get("wxString"),
            "metar_type": obs.get("metarType"),
            "temp_precise_c": temp_precise,
            "raw_metar": raw_metar,
        })
    return readings


# ---------------------------------------------------------------------------
# MetarCollector
# ---------------------------------------------------------------------------

class MetarCollector:
    """Polls aviationweather.gov for METAR observations."""

    def __init__(
        self,
        api_url: str,
        user_agent: str,
        hours_lookback: int,
        max_results: int,
        session_factory=None,
    ):
        self._api_url = api_url
        self._user_agent = user_agent
        self._hours_lookback = hours_lookback
        self._max_results = max_results
        self._session_factory = session_factory
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": self._user_agent},
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    async def fetch_metar(self, station_ids: list[str]) -> list[dict]:
        """Fetch METAR data from aviationweather.gov for given stations.

        Returns parsed readings list (not yet stored in DB).
        """
        if not self._http or not station_ids:
            return []

        params = {
            "ids": ",".join(station_ids),
            "format": "json",
            "hours": self._hours_lookback,
            "taf": "false",
        }
        try:
            resp = await self._http.get(self._api_url, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "METAR API error %d: %s", resp.status_code, resp.text[:200]
                )
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            return parse_metar_response(data)
        except Exception:
            logger.exception("METAR fetch failed for %d stations", len(station_ids))
            return []

    async def fetch_and_store(self, station_ids: list[str]) -> int:
        """Fetch METAR data and store new readings in the database.

        Deduplicates by (station_id, observed_at). Returns count of new readings stored.
        """
        readings = await self.fetch_metar(station_ids)
        if not readings or not self._session_factory:
            return 0

        from sqlalchemy import select, update

        from polymarket_weather.db.models import IcaoStation, MetarReading

        stored = 0
        # Track the freshest new observed_at per station so we only update
        # last_report_at for stations that had NEW data in this batch. Fix 1.7.
        freshest: dict[str, datetime] = {}
        async with self._session_factory() as session:
            for r in readings:
                # Check for duplicate (station_id + observed_at)
                exists = await session.execute(
                    select(MetarReading.id).where(
                        MetarReading.station_id == r["station_id"],
                        MetarReading.observed_at == r["observed_at"],
                    )
                )
                if exists.scalar_one_or_none() is not None:
                    continue

                reading = MetarReading(
                    station_id=r["station_id"],
                    observed_at=r["observed_at"],
                    fetched_at=datetime.now(timezone.utc),
                    temp=r["temp"],
                    dewp=r["dewp"],
                    wdir=r["wdir"],
                    wspd=r["wspd"],
                    wgst=r["wgst"],
                    altim=r["altim"],
                    slp_hpa=r.get("slp"),
                    visib=r["visib"],
                    cloud_cover=r["cloud_cover"],
                    wx_string=r["wx_string"],
                    metar_type=r["metar_type"],
                    temp_precise_c=r["temp_precise_c"],
                    raw_metar=r["raw_metar"],
                )
                session.add(reading)
                stored += 1
                # Track only stations that produced a fresh row so we don't
                # mark duplicate-only stations as recently reporting. Fix 1.7.
                prev = freshest.get(r["station_id"])
                if prev is None or r["observed_at"] > prev:
                    freshest[r["station_id"]] = r["observed_at"]

            if stored > 0:
                # Update last_report_at ONLY for stations that had new readings
                for sid, observed_at in freshest.items():
                    await session.execute(
                        update(IcaoStation)
                        .where(IcaoStation.station_id == sid)
                        .values(last_report_at=observed_at)
                    )
                await session.commit()

        return stored

    async def check_staleness(
        self, station_ids: list[str], stale_threshold_seconds: int
    ) -> list[str]:
        """Return station IDs that haven't reported within the threshold."""
        if not self._session_factory:
            return []

        from sqlalchemy import select

        from polymarket_weather.db.models import IcaoStation

        stale = []
        cutoff = datetime.now(timezone.utc).timestamp() - stale_threshold_seconds
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

        async with self._session_factory() as session:
            for sid in station_ids:
                result = await session.execute(
                    select(IcaoStation.last_report_at).where(
                        IcaoStation.station_id == sid
                    )
                )
                last_report = result.scalar_one_or_none()
                if last_report is None or last_report < cutoff_dt:
                    stale.append(sid)

        return stale
