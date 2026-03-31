"""Crypto Price Signal — compare real-time spot prices to Polymarket daily/weekly crypto markets."""

import asyncio
import logging
import math
import re
from datetime import datetime, timezone

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

# Regex for parsing crypto market questions like:
#   "Will Bitcoin be above $100,000 on March 31?"
#   "Will BTC be above $95,000.50 on April 1, 2025?"
#   "Bitcoin above $100k?"
#   "Will ETH be below $3,000?"
CRYPTO_MARKET_PATTERN = re.compile(
    r"(?:will\s+)?(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple)"
    r".*?(?P<dir>above|over|below|under|exceed|reach|hit)\s*\$?(?P<strike>[\d,]+\.?\d*k?)",
    re.IGNORECASE,
)

ASSET_SYMBOLS = {
    "bitcoin": "BTC/USDT",
    "btc": "BTC/USDT",
    "ethereum": "ETH/USDT",
    "eth": "ETH/USDT",
    "solana": "SOL/USDT",
    "sol": "SOL/USDT",
    "xrp": "XRP/USDT",
    "ripple": "XRP/USDT",
}

_BELOW_KEYWORDS = {"below", "under"}


def parse_strike_price(strike_str: str) -> float | None:
    """Parse strike price string, handling commas and 'k' suffix."""
    s = strike_str.replace(",", "")
    if s.lower().endswith("k"):
        try:
            return float(s[:-1]) * 1000
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_market_question(question: str) -> tuple[str, float, bool] | None:
    """Parse crypto market question into (ccxt_symbol, strike_price, is_above).

    Returns None if this is not a recognizable crypto price market.
    """
    match = CRYPTO_MARKET_PATTERN.search(question)
    if not match:
        return None
    asset = match.group("asset").lower()
    symbol = ASSET_SYMBOLS.get(asset)
    if not symbol:
        return None
    strike = parse_strike_price(match.group("strike"))
    if strike is None or strike <= 0:
        return None
    direction_word = match.group("dir").lower()
    is_above = direction_word not in _BELOW_KEYWORDS
    return symbol, strike, is_above


def sigmoid(x: float, steepness: float = 20.0) -> float:
    """Sigmoid mapping from distance-from-strike to implied probability.

    x=0 → 0.5, large positive → ~1.0, large negative → ~0.0.
    steepness controls how quickly it transitions.
    """
    clamped = max(-10, min(10, steepness * x))
    return 1.0 / (1.0 + math.exp(-clamped))


def time_adjusted_steepness(days_to_expiry: float, base_steepness: float = 20.0) -> float:
    """Scale sigmoid steepness inversely with sqrt of time-to-expiry.

    Near expiry → steeper (more confident about current price holding).
    Far from expiry → flatter (more room for price reversal).

    With base_steepness=20 calibrated for 1-day markets:
      6h  → steepness ~40 (very confident)
      1d  → steepness  20 (baseline)
      7d  → steepness ~7.6 (cautious)
    """
    days = max(days_to_expiry, 1 / 24)  # Floor at 1 hour to avoid division by zero
    return base_steepness / math.sqrt(days)


class CryptoPriceSignal(SignalPlugin):
    """Compare real-time crypto spot prices to Polymarket daily/weekly price markets."""

    def __init__(
        self,
        exchanges: list[str] | None = None,
        min_divergence: float = 0.05,
        max_days_to_expiry: int = 7,
        poll_interval: int = 60,
    ):
        self._exchange_names = exchanges or ["binance"]
        self._min_divergence = min_divergence
        self._max_days_to_expiry = max_days_to_expiry
        self._poll_interval = poll_interval
        self._exchanges: list = []
        self._price_cache: dict[str, float] = {}
        self._running = False
        self._refresh_task: asyncio.Task | None = None
        self._price_monitor = None  # Optional PriceMonitor for WebSocket cache

    @property
    def name(self) -> str:
        return "crypto_price"

    async def start(self) -> None:
        try:
            import ccxt.async_support as ccxt
        except ImportError:
            logger.warning("ccxt not installed — crypto_price signal disabled")
            return
        for name in self._exchange_names:
            exchange_class = getattr(ccxt, name, None)
            if exchange_class:
                self._exchanges.append(exchange_class({"enableRateLimit": True}))
            else:
                logger.warning("Unknown exchange: %s", name)
        self._running = True
        self._refresh_task = asyncio.create_task(self._price_refresh_loop())

    async def stop(self) -> None:
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
        for ex in self._exchanges:
            try:
                await ex.close()
            except Exception:
                pass

    def set_price_monitor(self, monitor) -> None:
        """Set optional PriceMonitor for WebSocket-cached prices."""
        self._price_monitor = monitor

    def can_evaluate(self, market: Market) -> bool:
        return parse_market_question(market.question) is not None

    async def evaluate(self, market: Market) -> Signal | None:
        parsed = parse_market_question(market.question)
        if parsed is None:
            return None

        symbol, strike, is_above = parsed
        now = datetime.now(timezone.utc)

        # Only target short-duration markets (daily/weekly)
        days_to_expiry = (market.end_date - now).total_seconds() / 86400
        if days_to_expiry > self._max_days_to_expiry or days_to_expiry < 0:
            return None

        spot_price = await self._get_spot_price(symbol)
        if spot_price is None:
            return None

        # Calculate implied probability via sigmoid of distance-from-strike
        # Steepness scales with time: steeper near expiry (confident), flatter far out (uncertain)
        pct_distance = (spot_price - strike) / strike
        steepness = time_adjusted_steepness(days_to_expiry)

        if is_above:
            implied_prob = sigmoid(pct_distance, steepness)
        else:
            implied_prob = 1.0 - sigmoid(pct_distance, steepness)

        divergence = implied_prob - market.current_price

        if abs(divergence) < self._min_divergence:
            return None

        direction = Direction.YES if divergence > 0 else Direction.NO
        confidence = min(abs(divergence) * 2.0, 0.75)

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=(
                f"Crypto: {symbol} spot=${spot_price:,.2f} vs strike=${strike:,.2f} "
                f"(implied={implied_prob:.0%}, market={market.current_price:.0%}, "
                f"div={divergence:+.1%}, {days_to_expiry:.1f}d to expiry)"
            ),
            timestamp=now,
        )

    async def _get_spot_price(self, symbol: str) -> float | None:
        """Get spot price from monitor cache, local cache, or exchange REST."""
        # Try WebSocket-backed monitor cache first
        if self._price_monitor:
            cached = self._price_monitor.get_cached_price("binance", symbol)
            if cached is not None:
                return cached

        # Fall back to local poll cache
        if symbol in self._price_cache:
            return self._price_cache[symbol]

        return await self._fetch_price(symbol)

    async def _fetch_price(self, symbol: str) -> float | None:
        """Fetch price from configured exchanges via REST."""
        for exchange in self._exchanges:
            try:
                ticker = await exchange.fetch_ticker(symbol)
                price = ticker.get("last")
                if price:
                    self._price_cache[symbol] = float(price)
                    return float(price)
            except Exception:
                logger.debug("Failed to fetch %s from %s", symbol, exchange.id)
        return None

    async def _price_refresh_loop(self) -> None:
        """Background loop to keep price cache fresh."""
        unique_symbols = list(set(ASSET_SYMBOLS.values()))
        while self._running:
            for sym in unique_symbols:
                try:
                    await self._fetch_price(sym)
                except Exception:
                    pass
            await asyncio.sleep(self._poll_interval)
