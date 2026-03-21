"""Thin/New Market Detection — identifies recently created markets with low volume.

LLMs have a genuine edge in thin markets where the price hasn't been set by
sophisticated traders yet. This module identifies markets created <48h ago with
<$10k volume and fast-tracks them for LLM analysis with a confidence boost.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx

from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Market, SignalEvent
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


class ThinMarketDetector:
    def __init__(
        self,
        event_bus: EventBus,
        llm_plugin: SignalPlugin | None = None,
        max_age_hours: int = 48,
        max_volume: float = 10000,
        confidence_boost: float = 1.15,
        poll_interval: int = 600,
    ):
        self._bus = event_bus
        self._llm_plugin = llm_plugin
        self._max_age_hours = max_age_hours
        self._max_volume = max_volume
        self._confidence_boost = confidence_boost
        self._poll_interval = poll_interval
        self._http: httpx.AsyncClient | None = None
        self._seen_ids: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        if self._http:
            await self._http.aclose()

    def is_thin_market(self, market: Market) -> bool:
        """Check if a market qualifies as thin/new."""
        now = datetime.now(timezone.utc)
        # We don't have created_at on Market, so use volume as primary signal
        # and check if it's relatively new by volume threshold
        return market.volume < self._max_volume and market.volume > 0

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                new_markets = await self._fetch_recent_markets()
                thin = [m for m in new_markets if self.is_thin_market(m)]
                for market in thin:
                    if market.id in self._seen_ids:
                        continue
                    self._seen_ids.add(market.id)
                    await self._fast_track_analysis(market)
            except Exception:
                logger.exception("Thin market detection failed")
            await asyncio.sleep(self._poll_interval)

    async def _fetch_recent_markets(self) -> list[Market]:
        """Fetch recently created markets from Gamma API."""
        if not self._http:
            return []
        try:
            resp = await self._http.get(
                f"{GAMMA_API}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 50,
                    "order": "startDate",
                    "ascending": "false",
                },
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            markets = []
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=self._max_age_hours)

            for m in data:
                # Check creation date
                start_str = m.get("startDate") or m.get("createdAt", "")
                if not start_str:
                    continue
                try:
                    start_date = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if start_date < cutoff:
                    continue

                condition_id = m.get("conditionId") or m.get("condition_id", "")
                question = m.get("question", "")
                if not condition_id or not question:
                    continue

                # Parse price
                current_price = 0.5
                no_price = 0.0
                outcome_prices = m.get("outcomePrices")
                if outcome_prices:
                    try:
                        import json
                        if isinstance(outcome_prices, str):
                            outcome_prices = json.loads(outcome_prices)
                        if outcome_prices:
                            current_price = float(outcome_prices[0])
                        if len(outcome_prices) >= 2:
                            no_price = float(outcome_prices[1])
                    except Exception:
                        pass

                # Parse tokens
                tokens = {}
                raw_clob = m.get("clobTokenIds")
                if raw_clob:
                    try:
                        import json
                        clob_ids = json.loads(raw_clob) if isinstance(raw_clob, str) else raw_clob
                        if len(clob_ids) >= 2 and isinstance(clob_ids[0], str):
                            tokens = {"YES": clob_ids[0], "NO": clob_ids[1]}
                    except Exception:
                        pass

                # Parse end date
                end_str = m.get("endDate", "")
                try:
                    end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    end_date = datetime(2030, 1, 1, tzinfo=timezone.utc)

                volume = 0.0
                try:
                    volume = float(m.get("volume", 0) or 0)
                except (ValueError, TypeError):
                    pass

                category = m.get("category", "").lower() if m.get("category") else ""

                markets.append(Market(
                    id=condition_id,
                    question=question,
                    end_date=end_date,
                    tokens=tokens,
                    current_price=current_price,
                    no_price=no_price,
                    category=category,
                    volume=volume,
                ))
            return markets
        except Exception:
            logger.debug("Failed to fetch recent markets")
            return []

    async def _fast_track_analysis(self, market: Market) -> None:
        """Run LLM analysis on a thin market with boosted confidence."""
        if not self._llm_plugin:
            return

        logger.info("Thin market detected: %s (vol: $%.0f) — fast-tracking LLM analysis",
                    market.question[:60], market.volume)

        try:
            signal = await self._llm_plugin.evaluate(market)
            if signal is None:
                return

            # Apply confidence boost for thin markets (LLMs have more edge here)
            boosted_confidence = min(signal.confidence * self._confidence_boost, 0.99)

            from polymarket_bot.models import Signal
            boosted_signal = Signal(
                source=signal.source,
                market_id=signal.market_id,
                direction=signal.direction,
                confidence=round(boosted_confidence, 3),
                reasoning=f"[THIN MARKET boost] {signal.reasoning}",
                timestamp=signal.timestamp,
            )

            event = SignalEvent(signal=boosted_signal, market=market)
            await self._bus.publish("signal", event)
            logger.info("Thin market signal: %s %s conf=%.2f (boosted from %.2f)",
                       boosted_signal.direction.value, market.id[:16],
                       boosted_confidence, signal.confidence)
        except Exception:
            logger.debug("Thin market LLM analysis failed for %s", market.id[:16])
