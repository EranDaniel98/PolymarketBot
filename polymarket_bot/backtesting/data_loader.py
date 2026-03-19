"""Fetch resolved markets from Polymarket Gamma API for backtesting."""

import logging

import httpx

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class HistoricalDataLoader:
    async def fetch_resolved_markets(self, limit: int = 100) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{GAMMA_API}/events",
                params={
                    "closed": "true",
                    "limit": limit,
                    "order": "closed_time",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
            events = resp.json()

        markets = []
        for event in events:
            event_category = (
                event.get("tags", [{}])[0].get("label", "")
                if event.get("tags") else ""
            )
            for m in event.get("markets", []):
                outcome = m.get("outcome", m.get("winner", ""))
                if outcome:
                    markets.append({
                        "question": m.get("question", ""),
                        "condition_id": m.get("conditionId", ""),
                        "outcome": outcome,
                        "outcome_prices": m.get("outcomePrices", []),
                        "end_date": m.get("endDate", ""),
                        "category": event_category,
                        "volume": m.get("volume", m.get("volume24hr", 0)),
                        "description": m.get("description", "")[:200],
                    })
        return markets
