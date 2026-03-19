import asyncio
import logging
import random
from datetime import datetime, timezone

from polymarket_bot.config import ExecutionConfig
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus
from polymarket_bot.arbitrage.structural_arb import StructuralArbOpportunity
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
                key=private_key,
                chain_id=chain_id,
            )

            if not self._config.paper_trading:
                creds = await asyncio.to_thread(
                    self._clob_client.create_or_derive_api_creds
                )
                self._clob_client.set_api_creds(creds)
                logger.info("CLOB client authenticated at Level 2")
            else:
                logger.info("CLOB client initialized in paper trading mode (Level 1)")
        except Exception:
            logger.exception("Failed to initialize CLOB client")

    async def stop(self) -> None:
        self._clob_client = None

    async def get_balance(self) -> float | None:
        if not self._clob_client:
            return None
        if self._config.paper_trading:
            return None  # No wallet balance in paper mode
        try:
            balance = await asyncio.to_thread(self._clob_client.get_balance_allowance)
            return float(balance.get("balance", 0)) / 1e6
        except Exception:
            logger.exception("Failed to fetch wallet balance")
            return None

    def check_slippage(self, target_price: float, actual_price: float) -> bool:
        if target_price <= 0:
            return False
        slippage = abs(actual_price - target_price) / target_price
        return slippage <= self._config.max_slippage

    async def _get_best_price(self, token_id: str, side: str) -> float | None:
        if not self._clob_client:
            return None
        try:
            result = await asyncio.to_thread(self._clob_client.get_price, token_id, side)
            return float(result.get("price", 0))
        except Exception:
            logger.debug("Failed to get best price for %s", token_id[:12])
            return None

    async def _place_order(
        self, tokens: dict[str, str], direction: Direction, amount: float,
        price: float, order_type: OrderType, is_exit: bool = False,
    ) -> tuple[str, float, OrderStatus]:
        # Paper trading — simulate order without touching CLOB
        if self._config.paper_trading:
            from uuid import uuid4
            order_id = f"paper_{uuid4().hex[:12]}"
            simulated_price = price * (1 + random.uniform(-0.003, 0.003))
            side = "SELL" if is_exit else "BUY"
            logger.info("[PAPER] %s %s @ $%.4f x $%.2f (id: %s)",
                        side, direction.value, simulated_price, amount, order_id)
            return order_id, simulated_price, OrderStatus.FILLED

        if not self._clob_client:
            raise RuntimeError("CLOB client not initialized")

        from py_clob_client.clob_types import (
            OrderArgs, MarketOrderArgs, PartialCreateOrderOptions,
        )
        from py_clob_client.clob_types import OrderType as ClobOrderType

        # Resolve token_id and side
        token_id = tokens.get(direction.value, tokens.get("YES", ""))
        side = "SELL" if is_exit else "BUY"

        # Get tick size and neg_risk (required by CLOB)
        tick_size = await asyncio.to_thread(self._clob_client.get_tick_size, token_id)
        neg_risk = await asyncio.to_thread(self._clob_client.get_neg_risk, token_id)
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        if order_type == OrderType.LIMIT:
            size = amount / price  # Convert USD to shares
            order_args = OrderArgs(
                token_id=token_id, price=price, size=size, side=side,
            )
            resp = await asyncio.to_thread(
                self._clob_client.create_and_post_order, order_args, options,
            )
        else:
            market_args = MarketOrderArgs(
                token_id=token_id, amount=amount, side=side, price=price,
            )
            signed = await asyncio.to_thread(
                self._clob_client.create_market_order, market_args, options,
            )
            resp = await asyncio.to_thread(
                self._clob_client.post_order, signed, ClobOrderType.FOK,
            )

        order_id = resp.get("orderID", resp.get("id", ""))
        logger.info("Order placed: %s %s %s @ $%.4f (id: %s)",
                    side, direction.value, token_id[:12], price, order_id)
        return order_id, price, OrderStatus.PLACED

    async def execute(self, decision: TradeDecision, current_price: float) -> None:
        tokens = decision.tokens
        is_exit = decision.is_exit

        if decision.order_type == OrderType.MARKET and tokens:
            token_id = tokens.get(decision.direction.value, tokens.get("YES", ""))
            side = "SELL" if is_exit else "BUY"
            best_price = await self._get_best_price(token_id, side)
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
                    tokens, decision.direction, decision.amount,
                    current_price, decision.order_type, is_exit,
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

                # For limit orders that are only placed (not yet filled), start repricing
                if status == OrderStatus.PLACED and decision.order_type == OrderType.LIMIT:
                    asyncio.create_task(self._reprice_loop(
                        order_id, decision, current_price,
                    ))

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

    async def _reprice_loop(
        self, order_id: str, decision: TradeDecision, original_price: float,
        max_reprices: int = 3, check_interval: int = 30,
    ) -> None:
        """Check fill status and reprice unfilled limit orders."""
        if self._config.paper_trading or not self._clob_client:
            return

        for attempt in range(max_reprices):
            await asyncio.sleep(check_interval)
            try:
                order = await asyncio.to_thread(self._clob_client.get_order, order_id)
                status = order.get("status", "")
                if status in ("FILLED", "MATCHED"):
                    logger.info("Order %s filled after %ds", order_id[:12], (attempt + 1) * check_interval)
                    return
                if status in ("CANCELLED", "EXPIRED"):
                    return

                # Cancel and reprice
                await asyncio.to_thread(self._clob_client.cancel, order_id)

                token_id = decision.tokens.get(decision.direction.value, decision.tokens.get("YES", ""))
                side = "SELL" if decision.is_exit else "BUY"
                new_price = await self._get_best_price(token_id, side)
                if not new_price:
                    return
                if not self.check_slippage(original_price, new_price):
                    logger.warning("Reprice slippage too high: %.4f -> %.4f", original_price, new_price)
                    return

                new_oid, _, new_status = await self._place_order(
                    decision.tokens, decision.direction, decision.amount,
                    new_price, OrderType.LIMIT, decision.is_exit,
                )
                order_id = new_oid
                logger.info("Order repriced (attempt %d): %s @ %.4f", attempt + 1, new_oid[:12], new_price)
            except Exception:
                logger.debug("Reprice attempt %d failed", attempt + 1)
                return

    async def execute_structural_arb(
        self, opportunity: StructuralArbOpportunity, amount_per_side: float,
        cancel_timeout: int = 60,
    ) -> None:
        """Place paired YES+NO limit orders for structural arbitrage."""
        tokens = opportunity.tokens

        # Paper trading — simulate both fills
        if self._config.paper_trading:
            from uuid import uuid4
            yes_id = f"paper_arb_yes_{uuid4().hex[:8]}"
            no_id = f"paper_arb_no_{uuid4().hex[:8]}"
            logger.info(
                "[PAPER] Structural arb: BUY YES @ $%.4f + BUY NO @ $%.4f, $%.2f/side, "
                "profit=%.2f%% (ids: %s, %s)",
                opportunity.yes_price, opportunity.no_price, amount_per_side,
                opportunity.expected_profit_pct * 100, yes_id, no_id,
            )
            for direction, price, order_id in [
                (Direction.YES, opportunity.yes_price, yes_id),
                (Direction.NO, opportunity.no_price, no_id),
            ]:
                execution = TradeExecution(
                    market_id=opportunity.market_id,
                    direction=direction,
                    amount=amount_per_side,
                    price=price,
                    order_id=order_id,
                    status=OrderStatus.FILLED,
                )
                await self._db.save_trade(execution)
                await self._bus.publish("trade_execution", execution)
            return

        if not self._clob_client:
            raise RuntimeError("CLOB client not initialized")

        # Place YES limit order
        try:
            yes_oid, yes_price, yes_status = await self._place_order(
                tokens, Direction.YES, amount_per_side,
                opportunity.yes_price, OrderType.LIMIT,
            )
        except Exception:
            logger.exception("Structural arb: YES order failed")
            return

        # Place NO limit order
        try:
            no_oid, no_price, no_status = await self._place_order(
                tokens, Direction.NO, amount_per_side,
                opportunity.no_price, OrderType.LIMIT,
            )
        except Exception:
            logger.exception("Structural arb: NO order failed, cancelling YES")
            try:
                await asyncio.to_thread(self._clob_client.cancel, yes_oid)
            except Exception:
                logger.warning("Failed to cancel YES leg %s", yes_oid)
            return

        # Monitor both legs — cancel unfilled after timeout
        asyncio.create_task(self._monitor_arb_legs(yes_oid, no_oid, cancel_timeout))

        for direction, price, order_id in [
            (Direction.YES, yes_price, yes_oid),
            (Direction.NO, no_price, no_oid),
        ]:
            execution = TradeExecution(
                market_id=opportunity.market_id,
                direction=direction,
                amount=amount_per_side,
                price=price,
                order_id=order_id,
                status=OrderStatus.PLACED,
            )
            await self._db.save_trade(execution)
            await self._bus.publish("trade_execution", execution)

    async def _monitor_arb_legs(
        self, yes_oid: str, no_oid: str, timeout: int,
    ) -> None:
        """Cancel unfilled arb legs after timeout."""
        if self._config.paper_trading or not self._clob_client:
            return
        await asyncio.sleep(timeout)
        for oid in (yes_oid, no_oid):
            try:
                order = await asyncio.to_thread(self._clob_client.get_order, oid)
                status = order.get("status", "")
                if status not in ("FILLED", "MATCHED"):
                    await asyncio.to_thread(self._clob_client.cancel, oid)
                    logger.info("Cancelled unfilled arb leg %s (status: %s)", oid[:12], status)
            except Exception:
                logger.debug("Failed to check/cancel arb leg %s", oid[:12])
