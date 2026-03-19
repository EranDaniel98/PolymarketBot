import json
import logging
from datetime import datetime, timezone

import anthropic
import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are an expert prediction market trader. Your job is to estimate the TRUE probability of YES for this market, then identify if the current market price is wrong (= a profitable trade).

## Market
Question: {question}
Current YES price: {price:.1%}
End date: {end_date}
Category: {category}

## Recent News Headlines
{news_context}

## Reddit Sentiment
{reddit_context}

## Bookmaker/External Odds
{odds_context}

## Your Task
1. Analyze ALL the evidence above
2. Estimate the true probability of YES (0.0 to 1.0)
3. Compare to the current market price of {price:.1%}
4. Only flag an edge if you're confident the market is meaningfully wrong

Think step by step, then respond with ONLY this JSON:
{{"probability": <float 0.0-1.0>, "confidence": <float 0.0-1.0 how sure you are>, "reasoning": "<2-3 sentences>"}}

IMPORTANT: If the evidence is ambiguous or you're unsure, set confidence LOW (< 0.3). Only high confidence if evidence clearly points one way."""


class LLMSignal(SignalPlugin):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6-20250514"):
        self._api_key = api_key
        self._model = model
        self._client: anthropic.AsyncAnthropic | None = None
        self._http: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "llm"

    async def start(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        if self._http:
            await self._http.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        if not self._client:
            return None

        try:
            # Gather real context from multiple sources
            news_context = await self._gather_news(market.question)
            reddit_context = await self._gather_reddit(market.question)
            odds_context = await self._gather_odds(market.question)

            prompt = PROMPT_TEMPLATE.format(
                question=market.question,
                price=market.current_price,
                end_date=market.end_date.strftime("%Y-%m-%d"),
                category=market.category or "general",
                news_context=news_context or "No recent news found.",
                reddit_context=reddit_context or "No Reddit discussion found.",
                odds_context=odds_context or "No external odds available.",
            )

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            # Extract JSON from response (handle markdown code blocks)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            probability = float(parsed["probability"])
            llm_confidence = float(parsed.get("confidence", 0.5))
            reasoning = parsed.get("reasoning", "")

            if not 0.0 <= probability <= 1.0:
                logger.warning("LLM returned invalid probability: %s", probability)
                return None

            edge = probability - market.current_price
            if abs(edge) < 0.03:
                return None

            # Combine edge size with LLM's self-reported confidence
            direction = Direction.YES if edge > 0 else Direction.NO
            edge_confidence = min(abs(edge) * 2, 0.95)
            confidence = edge_confidence * llm_confidence  # Dampen by LLM's own certainty

            if confidence < 0.1:
                return None

            return Signal(
                source=self.name,
                market_id=market.id,
                direction=direction,
                confidence=round(confidence, 3),
                reasoning=f"LLM: {probability:.0%} vs market {market.current_price:.0%} "
                          f"(edge {edge:+.1%}, self-conf {llm_confidence:.0%}). {reasoning}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("LLM signal evaluation failed")
            return None

    async def _gather_news(self, query: str) -> str:
        """Fetch recent headlines via a free news endpoint."""
        if not self._http:
            return ""
        keywords = " ".join(query.replace("?", "").split()[:6])
        try:
            resp = await self._http.get(
                "https://newsapi.org/v2/everything",
                params={"q": keywords, "sortBy": "publishedAt", "pageSize": 5,
                        "apiKey": ""},  # Will 401 without key, that's OK
            )
            if resp.status_code != 200:
                return ""
            articles = resp.json().get("articles", [])
            lines = []
            for a in articles[:5]:
                title = a.get("title", "")
                desc = a.get("description", "")
                lines.append(f"- {title}: {desc[:150]}")
            return "\n".join(lines)
        except Exception:
            return ""

    async def _gather_reddit(self, query: str) -> str:
        """Fetch recent Reddit posts via public JSON."""
        if not self._http:
            return ""
        keywords = " ".join(query.replace("?", "").split()[:5])
        try:
            resp = await self._http.get(
                "https://www.reddit.com/search.json",
                params={"q": keywords, "sort": "relevance", "t": "week", "limit": 10},
            )
            if resp.status_code != 200:
                return ""
            children = resp.json().get("data", {}).get("children", [])
            lines = []
            for c in children[:10]:
                d = c.get("data", {})
                title = d.get("title", "")
                score = d.get("score", 0)
                comments = d.get("num_comments", 0)
                lines.append(f"- [{score} pts, {comments} comments] {title}")
            return "\n".join(lines)
        except Exception:
            return ""

    async def _gather_odds(self, query: str) -> str:
        """Placeholder for external odds — returns empty for now."""
        return ""
