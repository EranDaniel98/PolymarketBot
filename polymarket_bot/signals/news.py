import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class NewsSignal(SignalPlugin):
    def __init__(self, api_key: str, poll_interval: int = 300):
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "news"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        articles = await self._fetch_articles(market.question)
        if not articles:
            return None

        direction, confidence, reasoning = await self._analyze_sentiment(articles, market)
        if confidence < 0.1:
            return None

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_articles(self, query: str) -> list[dict]:
        if not self._client:
            return []
        keywords = self._extract_keywords(query)
        try:
            resp = await self._client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": keywords,
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("articles", [])
        except Exception:
            logger.exception("Failed to fetch news articles")
            return []

    def _extract_keywords(self, question: str) -> str:
        stop_words = {"will", "the", "a", "an", "by", "in", "of", "to", "is", "be", "at", "on"}
        words = question.replace("?", "").split()
        keywords = [w for w in words if w.lower() not in stop_words]
        return " ".join(keywords[:5])

    async def _analyze_sentiment(
        self, articles: list[dict], market: Market
    ) -> tuple[Direction, float, str]:
        positive = 0
        negative = 0
        titles = []

        for article in articles:
            title = (article.get("title") or "").lower()
            desc = (article.get("description") or "").lower()
            text = f"{title} {desc}"
            titles.append(article.get("title", ""))

            positive_words = ["surge", "rise", "gain", "win", "pass", "approve", "success", "rally", "bullish", "up"]
            negative_words = ["fall", "drop", "lose", "fail", "reject", "crash", "bearish", "down", "decline"]

            positive += sum(1 for w in positive_words if w in text)
            negative += sum(1 for w in negative_words if w in text)

        total = positive + negative
        if total == 0:
            return Direction.YES, 0.0, "No clear sentiment"

        if positive > negative:
            ratio = positive / total
            confidence = min(ratio * 0.9, 0.95)
            direction = Direction.YES
        else:
            ratio = negative / total
            confidence = min(ratio * 0.9, 0.95)
            direction = Direction.NO

        reasoning = f"Analyzed {len(articles)} articles. Sentiment: {positive}+ / {negative}-. Headlines: {'; '.join(titles[:3])}"
        return direction, round(confidence, 3), reasoning
