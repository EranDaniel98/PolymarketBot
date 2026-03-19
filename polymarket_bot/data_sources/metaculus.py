"""Shared Metaculus data source with response caching."""

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MetaculusForecast:
    question: str
    community_prediction: float | None
    forecaster_count: int
    url: str


class MetaculusClient:
    def __init__(self, cache_ttl: int = 300):
        self._cache: dict[str, tuple[float, list[MetaculusForecast]]] = {}
        self._cache_ttl = cache_ttl
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    async def search(self, query: str, limit: int = 5) -> list[MetaculusForecast]:
        keywords = " ".join(query.replace("?", "").split()[:5])
        cache_key = keywords.lower()

        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[0] < self._cache_ttl:
            return cached[1]

        results = await self._fetch(keywords, limit)
        self._cache[cache_key] = (time.time(), results)
        return results

    async def _fetch(self, keywords: str, limit: int) -> list[MetaculusForecast]:
        if not self._http:
            return []
        try:
            resp = await self._http.get(
                "https://www.metaculus.com/api2/questions/",
                params={"search": keywords, "status": "open", "limit": limit},
            )
            if resp.status_code != 200:
                return []
            results = resp.json().get("results", [])
            forecasts = []
            for q in results:
                prediction = q.get("community_prediction", {})
                if isinstance(prediction, dict):
                    median = prediction.get("full", {}).get("q2")
                else:
                    median = None
                forecasts.append(MetaculusForecast(
                    question=q.get("title", ""),
                    community_prediction=median,
                    forecaster_count=q.get("number_of_forecasters", 0),
                    url=q.get("url", ""),
                ))
            return forecasts
        except Exception:
            logger.debug("Metaculus search failed for: %s", keywords)
            return []

    def format_for_llm(self, forecasts: list[MetaculusForecast]) -> str:
        lines = []
        for f in forecasts:
            if f.community_prediction is not None:
                lines.append(
                    f"- {f.question}: community={f.community_prediction:.0%} "
                    f"({f.forecaster_count} forecasters)"
                )
            else:
                lines.append(f"- {f.question}: ({f.forecaster_count} forecasters)")
        return "\n".join(lines)


# Module-level singleton for shared use
_default_client: MetaculusClient | None = None


async def get_metaculus_client() -> MetaculusClient:
    global _default_client
    if _default_client is None:
        _default_client = MetaculusClient()
        await _default_client.start()
    return _default_client
