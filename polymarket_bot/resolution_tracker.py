"""Resolution Tracker — polls Gamma API for market outcomes and backfills signal accuracy."""

import asyncio
import logging

import httpx

from polymarket_bot.database import Database

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class ResolutionTracker:
    def __init__(self, database: Database, poll_interval: int = 300):
        self._db = database
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._running = True
        self._http = httpx.AsyncClient(timeout=15)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        if self._http:
            await self._http.aclose()

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_resolutions()
            except Exception:
                logger.debug("Resolution tracker poll failed")
            await asyncio.sleep(self._poll_interval)

    async def _check_resolutions(self) -> None:
        unresolved = await self._db.get_unresolved_market_ids()
        if not unresolved:
            return

        for market_id in unresolved:
            outcome = await self._fetch_outcome(market_id)
            if outcome:
                await self._db.record_resolution(market_id, outcome)
                logger.info("Recorded resolution for %s: %s", market_id[:16], outcome)

    async def _fetch_outcome(self, market_id: str) -> str | None:
        if not self._http:
            return None
        try:
            resp = await self._http.get(f"{GAMMA_API}/markets/{market_id}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            outcome = data.get("outcome") or data.get("winner")
            if outcome and outcome in ("Yes", "No"):
                return outcome
            return None
        except Exception:
            return None
