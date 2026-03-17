import json
import logging
from datetime import datetime, timezone

import anthropic

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a prediction market analyst. Evaluate the following market and estimate the probability of YES.

Market question: {question}
Current market price (YES): {price:.0%}
Market end date: {end_date}

Respond with ONLY valid JSON:
{{"probability": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""


class LLMSignal(SignalPlugin):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6-20250514"):
        self._api_key = api_key
        self._model = model
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def name(self) -> str:
        return "llm"

    async def start(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()

    async def evaluate(self, market: Market) -> Signal | None:
        if not self._client:
            return None

        try:
            prompt = PROMPT_TEMPLATE.format(
                question=market.question,
                price=market.current_price,
                end_date=market.end_date.strftime("%Y-%m-%d"),
            )

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            parsed = json.loads(text)
            probability = float(parsed["probability"])
            reasoning = parsed.get("reasoning", "")

            if not 0.0 <= probability <= 1.0:
                logger.warning("LLM returned invalid probability: %s", probability)
                return None

            edge = probability - market.current_price
            if abs(edge) < 0.02:
                return None

            direction = Direction.YES if edge > 0 else Direction.NO
            confidence = min(abs(edge) * 2, 0.95)

            return Signal(
                source=self.name,
                market_id=market.id,
                direction=direction,
                confidence=round(confidence, 3),
                reasoning=f"LLM estimate: {probability:.0%} vs market: {market.current_price:.0%}. {reasoning}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("LLM signal evaluation failed")
            return None
