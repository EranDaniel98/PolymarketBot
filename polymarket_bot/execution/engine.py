import asyncio
import logging
import random
from datetime import datetime, timezone

from polymarket_bot.config import ExecutionConfig
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import (
    Direction, OrderStatus, OrderType, TradeDecision, TradeExecution,
)

logger = logging.getLogger(__name__)


class ExecutionEngine:
    def __init__(self, config: ExecutionConfig, database: Database, event_bus: EventBus):
        self._config = config
        self._db = database
        self._bus = event_bus
        self._clob_client = None

    async def start(self, api_key: str, api_secret: str, private_key: str, chain_id: int) -> None:
        try:
            from py_clob_client.client import ClobClient
            self._clob_client = ClobClient(
                host="https://clob.polymarket.com",
                key=api_key,
                chain_id=chain_id,
                funder=private_key,
            )
            logger.info("CLOB client initialized")
        except Exception:
            logger.exception("Failed to initialize CLOB client")

    async def stop(self) -> None:
        self._clob_client = None

    async def get_balance(self) -> float | None:
        if not self._clob_client:
            return None
        try:
            balance = self._clob_client.get_balance_allowance()
            return float(balance.get("balance", 0)) / 1e6
        except Exception:
            logger.exception("Failed to fetch wallet balance")
            return None

    def check_slippage(self, target_price: float, actual_price: float) -> bool:
        if target_price <= 0:
            return False
        slippage = abs(actual_price - target_price) / target_price
        return slippage <= self._config.max_slippage

    async def _get_best_price(self, market_id: str, direction: Direction) -> float | None:
        return None

    async def _place_order(
        self, market_id: str, direction: Direction, amount: float,
        price: float, order_type: OrderType,
    ) -> tuple[str, float, OrderStatus]:
        if not self._clob_client:
            raise RuntimeError("CLOB client not initialized")

        logger.info("Placing %s order: %s %s @ $%.4f x $%.2f",
                    order_type.value, direction.value, market_id, price, amount)
        return "order_placeholder", price, OrderStatus.PLACED

    async def execute(self, decision: TradeDecision, current_price: float) -> None:
        if decision.order_type == OrderType.MARKET:
            best_price = await self._get_best_price(decision.market_id, decision.direction)
            if best_price and not self.check_slippage(current_price, best_price):
                logger.warning(
                    "Slippage too high for %s: target=%.4f actual=%.4f",
                    decision.market_id, current_price, best_price,
                )
                return

        last_error = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                order_id, fill_price, status = await self._place_order(
                    decision.market_id, decision.direction, decision.amount,
                    current_price, decision.order_type,
                )

                execution = TradeExecution(
                    market_id=decision.market_id,
                    direction=decision.direction,
                    amount=decision.amount,
                    price=fill_price,
                    order_id=order_id,
                    status=status,
                )

                await self._db.save_trade(execution)
                await self._bus.publish("trade_execution", execution)
                logger.info("Trade executed: %s %s $%.2f @ $%.4f",
                           decision.direction.value, decision.market_id,
                           decision.amount, fill_price)
                return

            except Exception as e:
                last_error = str(e)
                logger.warning("Order attempt %d/%d failed: %s",
                             attempt, self._config.max_retries, e)
                if attempt < self._config.max_retries:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 1))

        execution = TradeExecution(
            market_id=decision.market_id,
            direction=decision.direction,
            amount=decision.amount,
            price=current_price,
            order_id="",
            status=OrderStatus.FAILED,
            error=last_error,
        )
        await self._db.save_trade(execution)
        logger.error("Trade failed after %d attempts: %s", self._config.max_retries, last_error)
