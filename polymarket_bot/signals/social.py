import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class SocialSignal(SignalPlugin):
    def __init__(self, reddit_client_id: str, reddit_client_secret: str, poll_interval: int = 600):
        self._reddit_id = reddit_client_id
        self._reddit_secret = reddit_client_secret
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None

    @property
    def name(self) -> str:
        return "social"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)
        await self._authenticate()

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def _authenticate(self) -> None:
        if not self._client:
            return
        try:
            resp = await self._client.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(self._reddit_id, self._reddit_secret),
                headers={"User-Agent": "PolymarketBot/0.1"},
            )
            resp.raise_for_status()
            self._access_token = resp.json().get("access_token")
        except Exception:
            logger.exception("Reddit authentication failed")

    async def evaluate(self, market: Market) -> Signal | None:
        posts = await self._fetch_reddit_posts(market.question)
        if not posts:
            return None

        direction, confidence, reasoning = self._analyze_posts(posts, market)
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

    async def _fetch_reddit_posts(self, query: str) -> list[dict]:
        if not self._client or not self._access_token:
            return []
        keywords = " ".join(query.replace("?", "").split()[:5])
        try:
            resp = await self._client.get(
                "https://oauth.reddit.com/search",
                params={"q": keywords, "sort": "relevance", "t": "day", "limit": 25},
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "User-Agent": "PolymarketBot/0.1",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [c["data"] for c in children]
        except Exception:
            logger.exception("Failed to fetch Reddit posts")
            return []

    def _analyze_posts(self, posts: list[dict], market: Market) -> tuple[Direction, float, str]:
        total_score = 0
        total_comments = 0
        positive = 0
        negative = 0

        for post in posts:
            title = (post.get("title") or "").lower()
            score = post.get("score", 0)
            comments = post.get("num_comments", 0)
            total_score += score
            total_comments += comments

            positive_words = ["bullish", "moon", "surge", "win", "yes", "gain", "up", "rally", "support"]
            negative_words = ["bearish", "crash", "dump", "lose", "no", "fail", "down", "decline", "reject"]

            if any(w in title for w in positive_words):
                positive += score
            elif any(w in title for w in negative_words):
                negative += score

        total = positive + negative
        if total == 0:
            return Direction.YES, 0.0, "No clear social sentiment"

        if positive >= negative:
            ratio = positive / total
            direction = Direction.YES
        else:
            ratio = negative / total
            direction = Direction.NO

        volume_factor = min(len(posts) / 25, 1.0)
        confidence = min(ratio * 0.8 * volume_factor, 0.90)

        reasoning = (
            f"Reddit: {len(posts)} posts, total score {total_score}, "
            f"{total_comments} comments. Sentiment: {positive}+ / {negative}-"
        )
        return direction, round(confidence, 3), reasoning
