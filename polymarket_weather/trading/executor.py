"""Trade executor — places orders on Polymarket CLOB. Adapted from existing bot."""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    order_id: str
    fill_price: float
    status: str         # "filled", "placed", "cancelled", "failed"
    error: str | None = None


class TradeExecutor:
    """Places orders on Polymarket CLOB. Supports paper and live trading."""

    def __init__(
        self,
        paper_trading: bool = True,
        paper_balance: float = 1000.0,
        max_slippage: float = 0.02,
        max_retries: int = 3,
    ):
        self._paper_trading = paper_trading
        self._paper_balance = paper_balance
        self._max_slippage = max_slippage
        self._max_retries = max_retries
        self._clob_client = None

    async def start(
        self, api_key: str = "", api_secret: str = "",
        private_key: str = "", chain_id: int = 137,
    ) -> None:
        if self._paper_trading:
            logger.info("Trade executor started in PAPER mode (balance: $%.2f)", self._paper_balance)
            return
        # Fail-fast: live mode must have a working CLOB client on startup.
        # The old code log-and-continued on error, leaving the bot running
        # with self._clob_client = None; any subsequent order attempt would
        # silently mis-behave. Fix 1.5.
        if not private_key:
            raise RuntimeError(
                "TradeExecutor: private_key is required when paper_trading=False"
            )
        from py_clob_client.client import ClobClient
        self._clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key, chain_id=chain_id,
        )
        creds = await asyncio.to_thread(self._clob_client.create_or_derive_api_creds)
        self._clob_client.set_api_creds(creds)
        logger.info("CLOB client authenticated (Level 2)")

    async def stop(self) -> None:
        self._clob_client = None

    def get_balance(self) -> float | None:
        if self._paper_trading:
            return self._paper_balance
        return None  # Live balance fetched separately

    def check_slippage(self, target_price: float, actual_price: float) -> bool:
        if target_price <= 0:
            return False
        slippage = abs(actual_price - target_price) / target_price
        return slippage <= self._max_slippage

    async def execute_order(
        self,
        token_id: str,
        side: str,         # "BUY" or "SELL"
        amount: float,
        price: float,
        order_type: str = "limit",
    ) -> OrderResult:
        """Execute an order. Paper mode simulates; live mode hits CLOB."""
        if self._paper_trading:
            return await self._paper_execute(token_id, side, amount, price)

        return await self._live_execute(token_id, side, amount, price, order_type)

    async def _paper_execute(
        self, token_id: str, side: str, amount: float, price: float,
    ) -> OrderResult:
        order_id = f"paper_{uuid4().hex[:12]}"
        sim_price = price * (1 + random.uniform(-0.003, 0.003))
        if side == "BUY":
            self._paper_balance -= amount
        else:
            self._paper_balance += amount
        logger.info("[PAPER] %s %s @ $%.4f x $%.2f (id: %s)",
                    side, token_id[:12], sim_price, amount, order_id)
        return OrderResult(order_id=order_id, fill_price=sim_price, status="filled")

    async def _live_execute(
        self, token_id: str, side: str, amount: float, price: float,
        order_type: str,
    ) -> OrderResult:
        if not self._clob_client:
            return OrderResult("", 0.0, "failed", error="CLOB client not initialized")

        from py_clob_client.clob_types import OrderArgs, PartialCreateOrderOptions

        last_error = None
        for attempt in range(1, self._max_retries + 1):
            try:
                tick_size = await asyncio.to_thread(self._clob_client.get_tick_size, token_id)
                neg_risk = await asyncio.to_thread(self._clob_client.get_neg_risk, token_id)
                options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

                size = amount / price if price > 0 else 0
                order_args = OrderArgs(
                    token_id=token_id, price=price, size=size, side=side,
                )
                resp = await asyncio.to_thread(
                    self._clob_client.create_and_post_order, order_args, options,
                )
                order_id = resp.get("orderID", resp.get("id", ""))
                logger.info("Order placed: %s %s @ $%.4f (id: %s)", side, token_id[:12], price, order_id)
                return OrderResult(order_id=order_id, fill_price=price, status="placed")
            except Exception as e:
                last_error = str(e)
                logger.warning("Order attempt %d/%d failed: %s", attempt, self._max_retries, e)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 1))

        return OrderResult("", 0.0, "failed", error=last_error)
