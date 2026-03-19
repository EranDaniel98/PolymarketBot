"""Manifold Markets data source — free, no auth required."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ManifoldMarket:
    question: str
    probability: float
    url: str


class ManifoldClient:
    def __init__(self):
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    async def search(self, query: str, limit: int = 5) -> list[ManifoldMarket]:
        if not self._http:
            return []
        keywords = " ".join(query.replace("?", "").split()[:6])
        try:
            resp = await self._http.get(
                "https://api.manifold.markets/v0/search-markets",
                params={"term": keywords, "limit": limit},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            results = []
            for m in data:
                prob = m.get("probability")
                if prob is not None:
                    results.append(ManifoldMarket(
                        question=m.get("question", ""),
                        probability=float(prob),
                        url=m.get("url", ""),
                    ))
            return results
        except Exception:
            logger.debug("Manifold search failed for: %s", keywords)
            return []
