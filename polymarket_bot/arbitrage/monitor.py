import asyncio
import json
import logging
import time

import httpx
import websockets

from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus

logger = logging.getLogger(__name__)

POLYMARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PriceMonitor:
    def __init__(
        self,
        mapper: MarketMapper,
        detector: OpportunityDetector,
        event_bus: EventBus,
        database: Database,
        poll_interval: int = 30,
    ):
        self._mapper = mapper
        self._detector = detector
        self._bus = event_bus
        self._db = database
        self._poll_interval = poll_interval
        self._running = False
        self._prices: dict[str, dict[str, float]] = {}
        self._price_timestamps: dict[str, dict[str, float]] = {}
        self._http_client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._max_price_age = poll_interval * 3

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30)
        self._ws_task = asyncio.create_task(self._subscribe_polymarket())
        self._poll_task = asyncio.create_task(self._poll_external_platforms())

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        if self._http_client:
            await self._http_client.aclose()

    def get_cached_price(self, platform: str, polymarket_id: str) -> float | None:
        price = self._prices.get(polymarket_id, {}).get(platform)
        if price is None:
            return None
        ts = self._price_timestamps.get(polymarket_id, {}).get(platform, 0)
        if time.time() - ts > self._max_price_age:
            logger.warning("Stale price for %s/%s (age %.0fs)", platform, polymarket_id, time.time() - ts)
            return None
        return price

    def _update_price(self, platform: str, polymarket_id: str, price: float) -> None:
        if polymarket_id not in self._prices:
            self._prices[polymarket_id] = {}
            self._price_timestamps[polymarket_id] = {}
        self._prices[polymarket_id][platform] = price
        self._price_timestamps[polymarket_id][platform] = time.time()

    async def _subscribe_polymarket(self) -> None:
        market_ids = self._mapper.all_polymarket_ids()
        if not market_ids:
            logger.info("No market mappings configured — skipping Polymarket WS")
            return

        while self._running:
            try:
                async with websockets.connect(POLYMARKET_WS) as ws:
                    for mid in market_ids:
                        await ws.send(json.dumps({
                            "type": "subscribe",
                            "market": mid,
                        }))

                    async for message in ws:
                        if not self._running:
                            break
                        data = json.loads(message)
                        market_id = data.get("market")
                        price = data.get("price")
                        if market_id and price is not None:
                            self._update_price("polymarket", market_id, float(price))
            except Exception:
                logger.exception("Polymarket WS connection error — reconnecting in 5s")
                await asyncio.sleep(5)

    async def _poll_external_platforms(self) -> None:
        while self._running:
            for poly_id in self._mapper.all_polymarket_ids():
                mappings = self._mapper.get_mappings(poly_id)
                for platform, platform_id in mappings.items():
                    price = await self._fetch_platform_price(platform, platform_id)
                    if price is not None:
                        self._update_price(platform, poly_id, price)

                if poly_id in self._prices:
                    opp = self._detector.check(
                        polymarket_id=poly_id,
                        platform_prices=self._prices[poly_id],
                        market_ids={"polymarket": poly_id, **self._mapper.get_mappings(poly_id)},
                    )
                    if opp:
                        await self._bus.publish("arb_opportunity", opp)

            await asyncio.sleep(self._poll_interval)

    async def _fetch_platform_price(self, platform: str, platform_id: str) -> float | None:
        try:
            if platform == "kalshi":
                return await self._fetch_kalshi(platform_id)
            elif platform == "manifold":
                return await self._fetch_manifold(platform_id)
        except Exception:
            logger.exception("Failed to fetch %s price for %s", platform, platform_id)
        return None

    async def _fetch_kalshi(self, market_id: str) -> float | None:
        if not self._http_client:
            return None
        resp = await self._http_client.get(
            f"https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("market", {}).get("last_price")

    async def _fetch_manifold(self, market_id: str) -> float | None:
        if not self._http_client:
            return None
        resp = await self._http_client.get(
            f"https://api.manifold.markets/v0/market/{market_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("probability")
