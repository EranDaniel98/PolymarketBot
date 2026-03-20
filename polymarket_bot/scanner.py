"""Market discovery — fetches active markets from Polymarket's Gamma API."""

import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Market

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class MarketScanner:
    def __init__(self, max_markets: int = 50):
        self._max_markets = max_markets
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def fetch_active_markets(self) -> list[Market]:
        """Fetch active, open markets from Polymarket sorted by volume."""
        if not self._client:
            return []

        markets = []
        offset = 0
        limit = 100

        while len(markets) < self._max_markets:
            try:
                resp = await self._client.get(
                    f"{GAMMA_API}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "offset": offset,
                    },
                )
                resp.raise_for_status()
                events = resp.json()

                if not events:
                    break

                for event in events:
                    for m in event.get("markets", []):
                        market = self._parse_market(m, event)
                        if market:
                            markets.append(market)
                            if len(markets) >= self._max_markets:
                                break
                    if len(markets) >= self._max_markets:
                        break

                offset += limit
                if len(events) < limit:
                    break

            except Exception:
                logger.exception("Failed to fetch markets from Gamma API")
                break

        logger.info("Discovered %d active markets", len(markets))
        return markets

    def _parse_market(self, market_data: dict, event_data: dict) -> Market | None:
        try:
            condition_id = market_data.get("conditionId") or market_data.get("condition_id", "")
            if not condition_id:
                return None

            tokens = {}
            current_price = 0.5

            # Parse clobTokenIds — may be a JSON string or a list
            raw_clob = market_data.get("clobTokenIds", market_data.get("tokens"))
            clob_ids = []
            if isinstance(raw_clob, str):
                try:
                    import json as _json
                    clob_ids = _json.loads(raw_clob)
                except Exception:
                    pass
            elif isinstance(raw_clob, list):
                clob_ids = raw_clob

            # Handle list of dicts (token objects) or list of strings (token IDs)
            for token in clob_ids:
                if isinstance(token, dict):
                    outcome = token.get("outcome", "")
                    tokens[outcome] = token.get("token_id", "")
                    if outcome == "Yes":
                        current_price = float(token.get("price", 0.5))

            # If tokens are just a flat list of IDs (YES first, NO second)
            if not tokens and len(clob_ids) >= 2 and isinstance(clob_ids[0], str):
                tokens = {"YES": clob_ids[0], "NO": clob_ids[1]}

            # Try to get price from outcomePrices
            no_price = 0.0
            outcome_prices = market_data.get("outcomePrices")
            if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 1:
                try:
                    current_price = float(outcome_prices[0])
                except (ValueError, TypeError):
                    pass
                if len(outcome_prices) >= 2:
                    try:
                        no_price = float(outcome_prices[1])
                    except (ValueError, TypeError):
                        pass
            elif isinstance(outcome_prices, str):
                # Sometimes it's a JSON string
                try:
                    import json
                    prices = json.loads(outcome_prices)
                    if prices:
                        current_price = float(prices[0])
                    if len(prices) >= 2:
                        no_price = float(prices[1])
                except Exception:
                    pass

            question = market_data.get("question", event_data.get("title", ""))
            if not question:
                return None

            end_date_str = market_data.get("endDate") or market_data.get("end_date_iso")
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                except ValueError:
                    end_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
            else:
                end_date = datetime(2030, 1, 1, tzinfo=timezone.utc)

            # Derive category from event tags
            tags = event_data.get("tags", [])
            category = ""
            if tags:
                if isinstance(tags[0], dict):
                    category = tags[0].get("label", tags[0].get("slug", ""))
                else:
                    category = str(tags[0])

            # Parse description and volume
            description = market_data.get("description", "")
            volume = 0.0
            raw_volume = market_data.get("volume", market_data.get("volume24hr", 0))
            try:
                volume = float(raw_volume) if raw_volume else 0.0
            except (ValueError, TypeError):
                pass

            slug = event_data.get("slug", market_data.get("slug", ""))

            return Market(
                id=condition_id,
                question=question,
                end_date=end_date,
                tokens=tokens,
                current_price=current_price,
                no_price=no_price,
                category=category.lower(),
                description=description,
                volume=volume,
                slug=slug,
            )
        except Exception:
            logger.debug("Failed to parse market: %s", market_data.get("question", "unknown"))
            return None
