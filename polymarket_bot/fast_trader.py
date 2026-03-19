"""News-Driven Fast Trading — detects breaking news and trades within seconds."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.cli import console
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Direction, Market, OrderType, Signal, SignalEvent

logger = logging.getLogger(__name__)

# Keywords that suggest a market-resolving event
RESOLUTION_KEYWORDS = [
    "confirms", "confirmed", "announces", "announced", "officially",
    "wins", "won", "loses", "lost", "defeats", "defeated",
    "passes", "passed", "signed into law", "vetoed",
    "dies", "died", "resigns", "resigned", "fired", "arrested",
    "breaks record", "reaches", "surpasses", "falls below",
    "approves", "approved", "rejects", "rejected", "bans", "banned",
    "launches", "cancels", "cancelled",
]


class FastTrader:
    """Polls news rapidly and fires high-confidence signals on breaking events.

    Unlike the regular signal polling (every 2 min), this checks headlines
    every 15-30 seconds for market-moving events.
    """

    def __init__(
        self,
        event_bus: EventBus,
        markets: list[Market] | None = None,
        poll_interval: int = 20,
        newsapi_key: str = "",
    ):
        self._bus = event_bus
        self._markets = markets or []
        self._poll_interval = poll_interval
        self._newsapi_key = newsapi_key
        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._seen_headlines: set[str] = set()

    def update_markets(self, markets: list[Market]) -> None:
        self._markets = markets

    async def start(self) -> None:
        self._running = True
        self._client = httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )
        self._task = asyncio.create_task(self._poll_loop())
        console.print("[bold green]Fast trader started[/] (checking news every %ds)", self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_breaking_news()
            except Exception:
                logger.debug("Fast trader poll failed")
            await asyncio.sleep(self._poll_interval)

    async def _check_breaking_news(self) -> None:
        headlines = await self._fetch_latest_headlines()
        if not headlines:
            return

        new_headlines = [h for h in headlines if h["title"] not in self._seen_headlines]
        for h in headlines:
            self._seen_headlines.add(h["title"])

        # Keep seen set from growing unbounded
        if len(self._seen_headlines) > 5000:
            self._seen_headlines = set(list(self._seen_headlines)[-2000:])

        for headline in new_headlines:
            title = headline["title"].lower()

            # Check if this headline contains resolution-type language
            if not any(kw in title for kw in RESOLUTION_KEYWORDS):
                continue

            # Match against active markets
            for market in self._markets:
                relevance = self._compute_relevance(headline["title"], market)
                if relevance < 0.3:
                    continue

                direction = self._infer_direction(headline["title"], market)
                confidence = min(relevance * 0.9, 0.95)

                console.print(
                    f"[bold magenta]BREAKING[/] [{confidence:.0%}] "
                    f"{headline['title'][:80]} → {market.question[:50]}"
                )

                signal = Signal(
                    source="fast_news",
                    market_id=market.id,
                    direction=direction,
                    confidence=confidence,
                    reasoning=f"Breaking: {headline['title']}",
                    timestamp=datetime.now(timezone.utc),
                )

                event = SignalEvent(signal=signal, market=market)
                await self._bus.publish("signal", event)

    async def _fetch_latest_headlines(self) -> list[dict]:
        if not self._client:
            return []

        headlines = []

        # Try Reddit for real-time news (free, no auth)
        try:
            resp = await self._client.get(
                "https://www.reddit.com/r/news/new.json",
                params={"limit": 25},
            )
            if resp.status_code == 200:
                children = resp.json().get("data", {}).get("children", [])
                for c in children:
                    d = c.get("data", {})
                    headlines.append({
                        "title": d.get("title", ""),
                        "source": "reddit/r/news",
                        "score": d.get("score", 0),
                    })
        except Exception:
            pass

        # Also check r/worldnews
        try:
            resp = await self._client.get(
                "https://www.reddit.com/r/worldnews/new.json",
                params={"limit": 15},
            )
            if resp.status_code == 200:
                children = resp.json().get("data", {}).get("children", [])
                for c in children:
                    d = c.get("data", {})
                    headlines.append({
                        "title": d.get("title", ""),
                        "source": "reddit/r/worldnews",
                        "score": d.get("score", 0),
                    })
        except Exception:
            pass

        # NewsAPI if available
        if self._newsapi_key:
            try:
                resp = await self._client.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"country": "us", "pageSize": 20, "apiKey": self._newsapi_key},
                )
                if resp.status_code == 200:
                    for a in resp.json().get("articles", []):
                        headlines.append({
                            "title": a.get("title", ""),
                            "source": a.get("source", {}).get("name", "news"),
                            "score": 100,
                        })
            except Exception:
                pass

        return headlines

    def _compute_relevance(self, headline: str, market: Market) -> float:
        """How relevant is this headline to this market?"""
        headline_lower = headline.lower()
        question_words = set(market.question.lower().replace("?", "").split())
        # Remove common words
        stop_words = {"will", "the", "a", "an", "by", "in", "of", "to", "is", "be", "at",
                      "on", "it", "for", "and", "or", "not", "this", "that", "with"}
        question_words -= stop_words

        if not question_words:
            return 0.0

        matches = sum(1 for w in question_words if w in headline_lower)
        return matches / len(question_words)

    def _infer_direction(self, headline: str, market: Market) -> Direction:
        """Infer whether this headline suggests YES or NO for the market."""
        headline_lower = headline.lower()

        positive = ["wins", "won", "passes", "passed", "confirms", "confirmed",
                    "approves", "approved", "launches", "reaches", "surpasses",
                    "breaks record", "announces", "announced", "officially"]
        negative = ["loses", "lost", "defeats", "defeated", "rejects", "rejected",
                   "dies", "died", "resigns", "resigned", "fired", "cancels",
                   "cancelled", "bans", "banned", "vetoed", "falls below"]

        pos_count = sum(1 for w in positive if w in headline_lower)
        neg_count = sum(1 for w in negative if w in headline_lower)

        return Direction.YES if pos_count >= neg_count else Direction.NO
