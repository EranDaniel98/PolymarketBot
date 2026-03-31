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
        self._subscribed_ids: list[str] = []
        self._token_to_market: dict[str, str] = {}  # token_id -> condition_id

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30)
        self._ws_task = asyncio.create_task(self._subscribe_polymarket())
        self._poll_task = asyncio.create_task(self._poll_external_platforms())
        self._crypto_ws_task: asyncio.Task | None = asyncio.create_task(
            self._subscribe_crypto_exchanges()
        )

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        if hasattr(self, '_crypto_ws_task') and self._crypto_ws_task:
            self._crypto_ws_task.cancel()
        if self._http_client:
            await self._http_client.aclose()

    def subscribe_markets(self, market_ids: list[str],
                          token_to_market: dict[str, str] | None = None) -> None:
        if token_to_market:
            self._token_to_market.update(token_to_market)
        new_ids = [mid for mid in market_ids if mid not in self._subscribed_ids]
        if not new_ids:
            return
        self._subscribed_ids = list(set(self._subscribed_ids + market_ids))
        # Restart WS to subscribe new IDs
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            self._ws_task = asyncio.create_task(self._subscribe_polymarket())
        # Immediately fetch prices for newly subscribed markets
        asyncio.create_task(self._poll_polymarket_prices())
        logger.info("Subscribed to %d market price feeds", len(self._subscribed_ids))

    def get_cached_price(self, platform: str, polymarket_id: str) -> float | None:
        price = self._prices.get(polymarket_id, {}).get(platform)
        if price is None:
            return None
        ts = self._price_timestamps.get(polymarket_id, {}).get(platform, 0)
        if time.time() - ts > self._max_price_age:
            logger.warning("Stale price for %s/%s (age %.0fs)",
                          platform, polymarket_id, time.time() - ts)
            return None
        return price

    def _update_price(self, platform: str, polymarket_id: str, price: float) -> None:
        if polymarket_id not in self._prices:
            self._prices[polymarket_id] = {}
            self._price_timestamps[polymarket_id] = {}
        self._prices[polymarket_id][platform] = price
        self._price_timestamps[polymarket_id][platform] = time.time()

    async def _subscribe_polymarket(self) -> None:
        while self._running:
            # Collect all token IDs (not condition IDs) for subscription
            all_token_ids = list(self._token_to_market.keys())
            if not all_token_ids:
                logger.info("No token IDs to subscribe — waiting for markets")
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(POLYMARKET_WS) as ws:
                    # Polymarket WS expects: {"type": "market", "assets_ids": [...]}
                    await ws.send(json.dumps({
                        "type": "market",
                        "assets_ids": all_token_ids,
                        "custom_feature_enabled": True,
                    }))
                    logger.info("Polymarket WS subscribed to %d token feeds", len(all_token_ids))

                    async for message in ws:
                        if not self._running:
                            break
                        message = message.strip()
                        if not message or message == "[]":
                            continue  # Initial ack or empty update
                        try:
                            events = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        # Response can be a single event dict or a list of events
                        if isinstance(events, dict):
                            events = [events]
                        if not isinstance(events, list):
                            continue
                        for data in events:
                            token_id = str(data.get("asset_id", data.get("market", "")))
                            price = data.get("price", data.get("last_trade_price"))
                            if token_id and price is not None:
                                store_id = self._token_to_market.get(token_id, token_id)
                                self._update_price("polymarket", store_id, float(price))
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Polymarket WS connection error — reconnecting in 5s")
                await asyncio.sleep(5)

    async def _poll_external_platforms(self) -> None:
        while self._running:
            # Poll prices for all subscribed IDs via REST as fallback
            await self._poll_polymarket_prices()

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

    async def _poll_polymarket_prices(self) -> None:
        """Fallback: poll prices via REST for subscribed markets without recent WS data."""
        if not self._http_client:
            return
        for token_id in self._subscribed_ids:
            # Resolve to condition_id for storage
            condition_id = self._token_to_market.get(token_id, token_id)
            # Skip if we have a fresh price
            ts = self._price_timestamps.get(condition_id, {}).get("polymarket", 0)
            if time.time() - ts < self._poll_interval:
                continue
            try:
                resp = await self._http_client.get(
                    f"https://clob.polymarket.com/midpoint",
                    params={"token_id": token_id},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    mid = data.get("mid")
                    if mid is not None:
                        self._update_price("polymarket", condition_id, float(mid))
            except Exception:
                pass

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

    async def _subscribe_crypto_exchanges(self) -> None:
        """Stream real-time crypto spot prices via CCXT WebSocket (Binance).

        Prices are stored in the shared cache under platform="binance" with
        the CCXT symbol as key (e.g., "BTC/USDT"). The CryptoPriceSignal
        plugin can read these via get_cached_price("binance", "BTC/USDT").
        """
        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            logger.info("ccxt.pro not available — crypto exchange WS disabled")
            return

        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        exchange = ccxtpro.binance({"enableRateLimit": True})

        try:
            while self._running:
                for symbol in symbols:
                    try:
                        ticker = await exchange.watch_ticker(symbol)
                        price = ticker.get("last")
                        if price:
                            self._update_price("binance", symbol, float(price))
                            await self._bus.publish("crypto_price_update", {
                                "symbol": symbol,
                                "price": float(price),
                            })
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("Crypto WS error for %s", symbol, exc_info=True)
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await exchange.close()
            except Exception:
                pass
