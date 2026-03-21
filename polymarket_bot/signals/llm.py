import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert prediction market analyst. You estimate true probabilities and identify mispricings.

RULES:
- Respond with ONLY valid JSON — no preamble, no markdown, no explanation outside the JSON.
- Format: {"probability": <float 0.0-1.0>, "confidence": <float 0.0-1.0>, "reasoning": "<2-3 sentences>"}
- probability = your estimate of the TRUE chance of YES
- confidence = how sure you are about your estimate (NOT the probability itself)

CONFIDENCE GUIDELINES:
- 0.0-0.2: Very uncertain, limited/conflicting evidence
- 0.2-0.4: Some evidence but significant unknowns
- 0.4-0.6: Moderate evidence, reasonable directional view
- 0.6-0.8: Strong evidence from multiple independent sources
- 0.8-1.0: Near-certain, overwhelming concordant evidence (RARE — use sparingly)

DEFAULT TO LOW CONFIDENCE. Markets are usually efficient. Only claim high confidence with strong, specific evidence."""

PROMPT_TEMPLATE = """## Market
Question: {question}
Current YES price: {price:.1%}
End date: {end_date}
Category: {category}

## Market Details
{market_details}

## Related Markets (same event)
{related_markets}

## Recent News Headlines
{news_context}

## Reddit Sentiment
{reddit_context}

## Other Forecasting Platforms
{metaculus_context}

## Recent World Events
{wikipedia_context}

## Bookmaker/External Odds
{odds_context}

Analyze ALL evidence. Estimate the true probability of YES. Compare to the current market price of {price:.1%}. Only flag an edge if evidence clearly supports a different probability."""


SCREENING_PROMPT = """You are a prediction market screening tool. Given a market question and current price, quickly assess if there's likely mispricing.

Question: {question}
Current YES price: {price:.1%}
Category: {category}

Is this market likely mispriced? Respond with ONLY one of: YES, NO, or MAYBE followed by one sentence explaining why."""


@dataclass
class _ModelBackend:
    provider: str
    model: str
    weight: float
    client: object  # AsyncAnthropic or AsyncOpenAI

    async def query(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "anthropic":
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        elif self.provider == "openai":
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content
        raise ValueError(f"Unknown provider: {self.provider}")


def _parse_llm_response(text: str) -> dict | None:
    """Extract JSON with probability/confidence/reasoning from LLM response text."""
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    stripped = text.strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1:
            stripped = stripped[start:end + 1]
    try:
        parsed = json.loads(stripped)
        probability = float(parsed["probability"])
        confidence = float(parsed.get("confidence", 0.5))
        reasoning = parsed.get("reasoning", "")
        if not 0.0 <= probability <= 1.0:
            return None
        return {"probability": probability, "confidence": confidence, "reasoning": reasoning}
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _aggregate_trimmed_mean(results: list[dict]) -> dict:
    """Sort by probability, drop highest+lowest, average rest. <=2 results: simple average."""
    if not results:
        return {"probability": 0.5, "confidence": 0.0, "reasoning": ""}
    if len(results) <= 2:
        avg_prob = sum(r["probability"] for r in results) / len(results)
        avg_conf = sum(r["confidence"] for r in results) / len(results)
        reasons = "; ".join(r["reasoning"] for r in results if r["reasoning"])
        return {"probability": avg_prob, "confidence": avg_conf, "reasoning": reasons}
    sorted_results = sorted(results, key=lambda r: r["probability"])
    trimmed = sorted_results[1:-1]
    avg_prob = sum(r["probability"] for r in trimmed) / len(trimmed)
    avg_conf = sum(r["confidence"] for r in trimmed) / len(trimmed)
    reasons = "; ".join(r["reasoning"] for r in trimmed if r["reasoning"])
    return {"probability": avg_prob, "confidence": avg_conf, "reasoning": reasons}


def _aggregate_weighted(results: list[dict], weights: list[float]) -> dict:
    """Confidence-weighted average using backend weights."""
    if not results:
        return {"probability": 0.5, "confidence": 0.0, "reasoning": ""}
    total_w = sum(weights[:len(results)])
    if total_w == 0:
        total_w = 1.0
    avg_prob = sum(r["probability"] * w for r, w in zip(results, weights)) / total_w
    avg_conf = sum(r["confidence"] * w for r, w in zip(results, weights)) / total_w
    reasons = "; ".join(r["reasoning"] for r in results if r["reasoning"])
    return {"probability": avg_prob, "confidence": avg_conf, "reasoning": reasons}


class LLMSignal(SignalPlugin):
    def __init__(
        self, api_key: str, model: str = "claude-opus-4-6-20250514",
        screening_model: str = "claude-haiku-4-5-20250514", newsapi_key: str = "",
        openai_api_key: str = "", ensemble_enabled: bool = False,
        ensemble_models: list[dict] | None = None, aggregation: str = "trimmed_mean",
        confidence_discount: float = 1.0,
    ):
        self._api_key = api_key
        self._model = model
        self._screening_model = screening_model
        self._newsapi_key = newsapi_key
        self._openai_api_key = openai_api_key
        self._ensemble_enabled = ensemble_enabled
        self._ensemble_models = ensemble_models
        self._aggregation = aggregation
        self._confidence_discount = confidence_discount
        self._client: anthropic.AsyncAnthropic | None = None
        self._http: httpx.AsyncClient | None = None
        self._backends: list[_ModelBackend] = []

    @property
    def name(self) -> str:
        return "llm"

    async def start(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )
        self._backends = []
        if self._ensemble_enabled and self._ensemble_models:
            for spec in self._ensemble_models:
                provider = spec.get("provider", "anthropic")
                model_id = spec.get("model", self._model)
                weight = spec.get("weight", 1.0)
                if provider == "anthropic":
                    client = anthropic.AsyncAnthropic(api_key=self._api_key)
                elif provider == "openai":
                    from openai import AsyncOpenAI
                    client = AsyncOpenAI(api_key=self._openai_api_key)
                else:
                    logger.warning("Unknown provider %s, skipping", provider)
                    continue
                self._backends.append(_ModelBackend(
                    provider=provider, model=model_id, weight=weight, client=client,
                ))
            logger.info("Ensemble enabled with %d backends", len(self._backends))
        else:
            # Single-model backward-compatible mode
            self._backends = [_ModelBackend(
                provider="anthropic", model=self._model, weight=1.0, client=self._client,
            )]

    async def stop(self) -> None:
        for backend in self._backends:
            if hasattr(backend.client, "close"):
                await backend.client.close()
        self._backends = []
        if self._client:
            await self._client.close()
        if self._http:
            await self._http.aclose()

    async def _quick_screen(self, market: Market) -> bool:
        """Tier 1: Cheap Haiku screening to decide if deep analysis is worth it."""
        if not self._client:
            return False
        try:
            prompt = SCREENING_PROMPT.format(
                question=market.question,
                price=market.current_price,
                category=market.category or "general",
            )
            response = await self._client.messages.create(
                model=self._screening_model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip().upper()
            return text.startswith("YES") or text.startswith("MAYBE")
        except Exception:
            logger.debug("LLM screening failed, defaulting to analyze")
            return True  # On error, proceed with analysis

    async def evaluate(self, market: Market) -> Signal | None:
        if not self._client:
            return None

        try:
            # Tier 1: Quick screen with cheap model
            worth_analyzing = await self._quick_screen(market)
            if not worth_analyzing:
                return None

            # Tier 2: Deep analysis with full model(s)
            # Gather context from multiple sources (shared across all backends)
            news_context = await self._gather_news(market.question)
            reddit_context = await self._gather_reddit(market.question)
            odds_context = await self._gather_odds(market.question)
            market_details = await self._gather_polymarket_context(market)
            related_markets = await self._gather_related_markets(market)
            metaculus_context = await self._gather_metaculus(market.question)
            wikipedia_context = await self._gather_wikipedia()

            prompt = PROMPT_TEMPLATE.format(
                question=market.question,
                price=market.current_price,
                end_date=market.end_date.strftime("%Y-%m-%d"),
                category=market.category or "general",
                market_details=market_details or "No market details available.",
                related_markets=related_markets or "No related markets found.",
                news_context=news_context or "No recent news found.",
                reddit_context=reddit_context or "No Reddit discussion found.",
                metaculus_context=metaculus_context or "No forecasts found.",
                wikipedia_context=wikipedia_context or "No recent events found.",
                odds_context=odds_context or "No external odds available.",
            )

            # Fan out queries to all backends in parallel
            raw_results = await asyncio.gather(
                *[b.query(SYSTEM_PROMPT, prompt) for b in self._backends],
                return_exceptions=True,
            )

            # Parse responses, filter failures
            parsed_results = []
            parsed_weights = []
            for i, result in enumerate(raw_results):
                if isinstance(result, Exception):
                    logger.warning("Backend %s/%s failed: %s",
                                   self._backends[i].provider, self._backends[i].model, result)
                    continue
                parsed = _parse_llm_response(result)
                if parsed:
                    parsed_results.append(parsed)
                    parsed_weights.append(self._backends[i].weight)
                else:
                    logger.warning("Failed to parse response from %s/%s",
                                   self._backends[i].provider, self._backends[i].model)

            if not parsed_results:
                logger.warning("All LLM backends failed for %s", market.id)
                return None

            # Aggregate results
            if self._aggregation == "weighted_average":
                aggregated = _aggregate_weighted(parsed_results, parsed_weights)
            else:
                aggregated = _aggregate_trimmed_mean(parsed_results)

            probability = aggregated["probability"]
            llm_confidence = aggregated["confidence"] * self._confidence_discount
            reasoning = aggregated["reasoning"]

            edge = probability - market.current_price
            if abs(edge) < 0.03:
                return None

            # Combine edge size with LLM's self-reported confidence
            direction = Direction.YES if edge > 0 else Direction.NO
            edge_confidence = min(abs(edge) * 2, 0.95)
            confidence = edge_confidence * llm_confidence  # Dampen by LLM's own certainty

            if confidence < 0.1:
                return None

            n_backends = len(parsed_results)
            model_label = f"ensemble({n_backends})" if n_backends > 1 else self._backends[0].model

            return Signal(
                source=self.name,
                market_id=market.id,
                direction=direction,
                confidence=round(confidence, 3),
                reasoning=f"LLM[{model_label}]: {probability:.0%} vs market {market.current_price:.0%} "
                          f"(edge {edge:+.1%}, self-conf {llm_confidence:.0%}). {reasoning}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("LLM signal evaluation failed")
            return None

    async def _gather_news(self, query: str) -> str:
        """Fetch recent headlines via NewsAPI."""
        if not self._http or not self._newsapi_key:
            return ""
        keywords = " ".join(query.replace("?", "").split()[:6])
        try:
            resp = await self._http.get(
                "https://newsapi.org/v2/everything",
                params={"q": keywords, "sortBy": "publishedAt", "pageSize": 5,
                        "apiKey": self._newsapi_key},
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

    async def _gather_polymarket_context(self, market: Market) -> str:
        """Fetch market details from Gamma API — volume, liquidity, description."""
        if not self._http:
            return ""
        try:
            resp = await self._http.get(
                "https://gamma-api.polymarket.com/markets",
                params={"condition_id": market.id},
            )
            if resp.status_code != 200:
                return ""
            results = resp.json()
            if not results:
                return ""
            data = results[0] if isinstance(results, list) else results
            parts = []
            desc = data.get("description", "")
            if desc:
                parts.append(f"Description: {desc[:500]}")
            vol = data.get("volume", data.get("volume24hr", ""))
            if vol:
                parts.append(f"24h Volume: ${vol}")
            liq = data.get("liquidity", "")
            if liq:
                parts.append(f"Liquidity: ${liq}")
            return "\n".join(parts)
        except Exception:
            return ""

    async def _gather_related_markets(self, market: Market) -> str:
        """Fetch sibling markets from the same event."""
        if not self._http:
            return ""
        try:
            # Search for events containing this market
            resp = await self._http.get(
                "https://gamma-api.polymarket.com/events",
                params={"limit": 5, "active": "true"},
            )
            if resp.status_code != 200:
                return ""
            events = resp.json()
            for event in events:
                event_markets = event.get("markets", [])
                for em in event_markets:
                    cid = em.get("conditionId") or em.get("condition_id", "")
                    if cid == market.id and len(event_markets) > 1:
                        lines = []
                        for sibling in event_markets:
                            scid = sibling.get("conditionId") or sibling.get("condition_id", "")
                            if scid != market.id:
                                q = sibling.get("question", "")
                                prices = sibling.get("outcomePrices", [])
                                price_str = ""
                                if prices:
                                    try:
                                        if isinstance(prices, str):
                                            prices = json.loads(prices)
                                        price_str = f" (YES: {float(prices[0]):.0%})"
                                    except Exception:
                                        pass
                                lines.append(f"- {q}{price_str}")
                        return "\n".join(lines[:5])
            return ""
        except Exception:
            return ""

    async def _gather_metaculus(self, query: str) -> str:
        """Search Metaculus for similar forecasting questions using shared client."""
        try:
            from polymarket_bot.data_sources.metaculus import get_metaculus_client
            client = await get_metaculus_client()
            forecasts = await client.search(query, limit=5)
            return client.format_for_llm(forecasts)
        except Exception:
            return ""

    async def _gather_wikipedia(self) -> str:
        """Fetch current events from Wikipedia for world context."""
        if not self._http:
            return ""
        try:
            resp = await self._http.get(
                "https://en.wikipedia.org/api/rest_v1/page/html/Portal:Current_events",
                headers={"User-Agent": "PolymarketBot/0.1 (contact@example.com)"},
            )
            if resp.status_code != 200:
                return ""
            # Extract bullet points from HTML — simple regex for <li> content
            text = resp.text
            items = re.findall(r'<li[^>]*>(.*?)</li>', text, re.DOTALL)
            lines = []
            for item in items[:10]:
                # Strip HTML tags
                clean = re.sub(r'<[^>]+>', '', item).strip()
                if clean and len(clean) > 20:
                    lines.append(f"- {clean[:200]}")
            return "\n".join(lines[:8])
        except Exception:
            return ""
