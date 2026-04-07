"""Weather market scanner — discovers Polymarket weather markets via the Gamma API /events endpoint."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ScannedMarket:
    market_id: str          # conditionId
    question: str
    event_id: str           # Parent event ID for grouping
    yes_token_id: str
    no_token_id: str
    current_price: float    # YES price
    no_price: float         # NO price
    end_date: datetime | None
    resolution_source: str
    volume: float
    slug: str
    category: str


def parse_clob_tokens(raw) -> tuple[str, str] | None:
    """Parse clobTokenIds which may be JSON string or list.

    Returns (yes_token_id, no_token_id) or None if parsing fails.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, list):
        parsed = raw
    else:
        return None

    if not parsed or len(parsed) < 2:
        return None

    # Handle list of dicts or list of strings
    if isinstance(parsed[0], dict):
        yes = parsed[0].get("token_id", parsed[0].get("id", ""))
        no = parsed[1].get("token_id", parsed[1].get("id", ""))
    else:
        yes = str(parsed[0])
        no = str(parsed[1])

    return (yes, no) if yes and no else None


def parse_outcome_prices(raw) -> tuple[float, float]:
    """Parse outcomePrices which may be JSON string or list.

    Returns (yes_price, no_price). Defaults to (0.5, 0.5) on any failure.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return (0.5, 0.5)
    elif isinstance(raw, list):
        parsed = raw
    else:
        return (0.5, 0.5)

    try:
        yes = float(parsed[0]) if len(parsed) > 0 else 0.5
        no = float(parsed[1]) if len(parsed) > 1 else 1.0 - yes
    except (ValueError, TypeError, IndexError):
        return (0.5, 0.5)

    return (yes, no)


def parse_gamma_event(event: dict) -> list[ScannedMarket]:
    """Parse a Gamma API event into a list of ScannedMarket objects.

    Skips markets that are inactive or closed, or that lack token IDs.
    """
    event_id = str(event.get("id", ""))
    markets_data = event.get("markets", [])

    category = ""
    tags = event.get("tags", [])
    if tags and isinstance(tags, list) and isinstance(tags[0], dict):
        category = tags[0].get("label", "")

    results = []
    for m in markets_data:
        if not m.get("active", False) or m.get("closed", False):
            continue

        condition_id = m.get("conditionId", m.get("condition_id", ""))
        if not condition_id:
            continue

        tokens = parse_clob_tokens(m.get("clobTokenIds"))
        if not tokens:
            continue

        yes_price, no_price = parse_outcome_prices(m.get("outcomePrices"))

        end_date = None
        for date_field in ("endDateIso", "endDate"):
            raw_date = m.get(date_field)
            if raw_date:
                try:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    end_date = dt
                    break
                except (ValueError, TypeError):
                    continue

        results.append(ScannedMarket(
            market_id=condition_id,
            question=m.get("question", m.get("title", "")),
            event_id=event_id,
            yes_token_id=tokens[0],
            no_token_id=tokens[1],
            current_price=yes_price,
            no_price=no_price,
            end_date=end_date,
            resolution_source=m.get("resolutionSource", ""),
            volume=float(m.get("volume", 0) or 0),
            slug=m.get("slug", ""),
            category=category,
        ))

    return results


class WeatherMarketScanner:
    """Discovers and tracks Polymarket weather markets."""

    def __init__(
        self,
        gamma_api_url: str,
        discovery_endpoint: str = "/events",
        weather_tag_discovery: bool = True,
        fallback_keywords: list[str] | None = None,
        max_markets: int = 500,
    ):
        self._base_url = gamma_api_url
        self._discovery_endpoint = discovery_endpoint
        self._weather_tag_discovery = weather_tag_discovery
        self._fallback_keywords = fallback_keywords or ["temperature", "weather", "degrees"]
        self._max_markets = max_markets
        self._http: httpx.AsyncClient | None = None
        self._weather_tag_id: int | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        if self._weather_tag_discovery:
            await self._discover_weather_tag()

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    async def _discover_weather_tag(self) -> None:
        """Discover the numeric tag ID for 'weather' from Gamma API."""
        if not self._http:
            return
        try:
            resp = await self._http.get(f"{self._base_url}/tags")
            if resp.status_code == 200:
                tags = resp.json()
                for tag in tags:
                    if not isinstance(tag, dict):
                        continue
                    label = tag.get("label", "").lower()
                    slug = tag.get("slug", "").lower()
                    if label == "weather" or slug == "weather":
                        self._weather_tag_id = tag.get("id")
                        logger.info("Discovered weather tag ID: %s", self._weather_tag_id)
                        return
            logger.warning("Could not discover weather tag ID, will use keyword fallback")
        except Exception:
            logger.debug("Tag discovery failed, will use keyword fallback")

    async def fetch_weather_markets(self) -> list[ScannedMarket]:
        """Fetch all active weather markets from Polymarket."""
        if not self._http:
            return []

        all_markets: list[ScannedMarket] = []
        offset = 0
        limit = 100

        while len(all_markets) < self._max_markets:
            params: dict = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
            }
            if self._weather_tag_id is not None:
                params["tag_id"] = self._weather_tag_id

            from polymarket_weather.resilience import CircuitOpenError, get_breaker
            breaker = get_breaker("polymarket_gamma", failure_threshold=5, reset_timeout=120.0)
            try:
                resp = await breaker.call(
                    self._http.get,
                    f"{self._base_url}{self._discovery_endpoint}",
                    params=params,
                )
                if resp.status_code != 200:
                    break

                events = resp.json()
                if not events:
                    break

                for event in events:
                    markets = parse_gamma_event(event)
                    if self._weather_tag_id is not None:
                        # Already filtered by tag
                        all_markets.extend(markets)
                    else:
                        # Keyword filter fallback
                        for m in markets:
                            q = m.question.lower()
                            if any(kw in q for kw in self._fallback_keywords):
                                all_markets.append(m)

                offset += limit
                if len(events) < limit:
                    break

            except CircuitOpenError:
                logger.warning("Polymarket Gamma breaker open — skipping market scan")
                break
            except Exception:
                logger.exception("Market fetch failed at offset %d", offset)
                break

        logger.info("Scanned %d weather markets", len(all_markets))
        return all_markets[: self._max_markets]
