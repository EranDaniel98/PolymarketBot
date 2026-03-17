import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

SUBREDDITS = ["polymarket", "predictions", "wallstreetbets", "politics", "crypto", "sports"]


class SocialSignal(SignalPlugin):
    def __init__(self, subreddits: list[str] | None = None, poll_interval: int = 600):
        self._subreddits = subreddits or SUBREDDITS
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "social"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "PolymarketBot/0.1 (signal analysis)"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

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
        if not self._client:
            return []
        keywords = " ".join(query.replace("?", "").split()[:5])
        all_posts = []

        for subreddit in self._subreddits:
            try:
                resp = await self._client.get(
                    f"https://www.reddit.com/r/{subreddit}/search.json",
                    params={
                        "q": keywords,
                        "sort": "relevance",
                        "t": "day",
                        "restrict_sr": "on",
                        "limit": 10,
                    },
                )
                if resp.status_code == 429:
                    logger.warning("Reddit rate limited on r/%s — skipping", subreddit)
                    continue
                resp.raise_for_status()
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                all_posts.extend(c["data"] for c in children)
            except Exception:
                logger.debug("Failed to fetch from r/%s", subreddit)
                continue

        return all_posts

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
