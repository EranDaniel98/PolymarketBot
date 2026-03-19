"""Whale tracking signal — detects large wallet activity on Polymarket CLOB."""

import logging
import time
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

CLOB_API = "https://clob.polymarket.com"


class WhaleSignal(SignalPlugin):
    def __init__(
        self,
        single_trade_threshold: float = 10000,
        cumulative_threshold: float = 25000,
        window_seconds: int = 300,
        tracked_wallets: list[str] | None = None,
        poll_interval: int = 30,
    ):
        self._single_threshold = single_trade_threshold
        self._cumulative_threshold = cumulative_threshold
        self._window_seconds = window_seconds
        self._tracked_wallets = set(w.lower() for w in (tracked_wallets or []))
        self._poll_interval = poll_interval
        self._http: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, list[dict]]] = {}  # token_id -> (fetch_time, trades)

    @property
    def name(self) -> str:
        return "whale"

    def can_evaluate(self, market: Market) -> bool:
        return bool(market.tokens.get("YES"))

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "PolymarketBot/0.1"},
        )

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()

    async def _fetch_trades(self, token_id: str) -> list[dict]:
        """Fetch recent trades from CLOB API, using cache within poll interval."""
        now = time.time()
        cached = self._cache.get(token_id)
        if cached and (now - cached[0]) < self._poll_interval:
            return cached[1]

        if not self._http:
            return []

        try:
            resp = await self._http.get(
                f"{CLOB_API}/trades",
                params={"asset_id": token_id},
            )
            if resp.status_code != 200:
                return []
            trades = resp.json()
            if not isinstance(trades, list):
                trades = trades.get("data", [])
            self._cache[token_id] = (now, trades)
            return trades
        except Exception:
            logger.debug("Failed to fetch trades for %s", token_id[:12])
            return []

    async def evaluate(self, market: Market) -> Signal | None:
        if not self.can_evaluate(market):
            return None

        token_id = market.tokens["YES"]
        trades = await self._fetch_trades(token_id)
        if not trades:
            return None

        now_ts = time.time()
        cutoff = now_ts - self._window_seconds

        # Filter trades within window
        recent = []
        for t in trades:
            ts = t.get("timestamp") or t.get("match_time") or t.get("created_at", 0)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
            if isinstance(ts, (int, float)) and ts > cutoff:
                recent.append(t)

        if not recent:
            return None

        # Analyze trades
        single_whale = False
        whale_buys_usd = 0.0
        whale_sells_usd = 0.0
        total_whale_volume = 0.0

        # Group by maker address for cumulative check
        by_maker: dict[str, float] = {}

        for t in recent:
            size = float(t.get("size", t.get("amount", 0)))
            price = float(t.get("price", 0))
            usd_value = size * price if price > 0 else size
            side = t.get("side", "").upper()
            maker = (t.get("maker", "") or t.get("maker_address", "")).lower()

            # Check tracked wallets — halve threshold
            is_tracked = maker in self._tracked_wallets if maker else False
            effective_single = self._single_threshold / 2 if is_tracked else self._single_threshold

            if usd_value >= effective_single:
                single_whale = True
                total_whale_volume += usd_value
                if side == "BUY":
                    whale_buys_usd += usd_value
                else:
                    whale_sells_usd += usd_value

            if maker:
                by_maker[maker] = by_maker.get(maker, 0) + usd_value

        # Check cumulative threshold per maker
        for maker, total in by_maker.items():
            is_tracked = maker in self._tracked_wallets
            effective_cum = self._cumulative_threshold / 2 if is_tracked else self._cumulative_threshold
            if total >= effective_cum:
                total_whale_volume += total
                # Attribute direction from this maker's trades
                maker_buys = sum(
                    float(t.get("size", t.get("amount", 0))) * float(t.get("price", 0))
                    for t in recent
                    if (t.get("maker", "") or t.get("maker_address", "")).lower() == maker
                    and t.get("side", "").upper() == "BUY"
                )
                maker_sells = sum(
                    float(t.get("size", t.get("amount", 0))) * float(t.get("price", 0))
                    for t in recent
                    if (t.get("maker", "") or t.get("maker_address", "")).lower() == maker
                    and t.get("side", "").upper() != "BUY"
                )
                whale_buys_usd += maker_buys
                whale_sells_usd += maker_sells

        if total_whale_volume == 0 and not single_whale:
            return None

        # Direction = net direction of whale trades
        if whale_buys_usd > whale_sells_usd:
            direction = Direction.YES
        elif whale_sells_usd > whale_buys_usd:
            direction = Direction.NO
        else:
            return None

        # Confidence = min(total_whale_volume / (3 * threshold), 0.80)
        confidence = min(total_whale_volume / (3 * self._single_threshold), 0.80)

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=f"Whale activity: ${total_whale_volume:,.0f} detected "
                      f"(buys=${whale_buys_usd:,.0f}, sells=${whale_sells_usd:,.0f})",
            timestamp=datetime.now(timezone.utc),
        )
