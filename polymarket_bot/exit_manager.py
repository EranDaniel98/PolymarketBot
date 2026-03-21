"""Exit Strategy Engine — monitors open positions and triggers exits."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from polymarket_bot.cli import console, format_pnl
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import Direction, OrderType, TradeDecision

logger = logging.getLogger(__name__)


@dataclass
class ExitRule:
    take_profit: float = 0.25     # Exit when unrealized P&L > 25%
    stop_loss: float = -0.10      # Exit when unrealized P&L < -10% — cut losers fast
    edge_gone_threshold: float = 0.02  # Exit when market moves to within 2% of entry
    time_decay_hours: int = 24    # Start tightening stops after 24h
    trailing_stop: float = 0.08   # Trail 8% below peak unrealized P&L
    trailing_stop_activation: float = 0.03  # Activate trailing stop after 3% gain
    max_hold_hours: int = 168     # 7 days max hold for losing positions


@dataclass
class TrackedPosition:
    market_id: str
    direction: Direction
    entry_price: float
    amount: float
    entry_time: datetime
    peak_pnl_pct: float = 0.0
    tokens: dict[str, str] = field(default_factory=dict)
    end_date: datetime | None = None
    category: str = ""


class ExitManager:
    def __init__(
        self,
        event_bus: EventBus,
        database: Database,
        rules: ExitRule | None = None,
        check_interval: int = 30,
    ):
        self._bus = event_bus
        self._db = database
        self._rules = rules or ExitRule()
        self._check_interval = check_interval
        self._positions: dict[str, TrackedPosition] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._price_getter = None  # Set by app.py to monitor.get_cached_price

    def set_price_getter(self, fn) -> None:
        """Set function to get current price: fn(platform, market_id) -> float | None"""
        self._price_getter = fn

    async def track_entry(self, market_id: str, direction: Direction,
                          entry_price: float, amount: float,
                          tokens: dict[str, str] | None = None,
                          end_date: datetime | None = None,
                          category: str = "") -> None:
        self._positions[market_id] = TrackedPosition(
            market_id=market_id,
            direction=direction,
            entry_price=entry_price,
            amount=amount,
            entry_time=datetime.now(timezone.utc),
            tokens=tokens or {},
            end_date=end_date,
            category=category,
        )
        await self._db.save_position(
            market_id, direction.value, amount, entry_price,
            tokens=json.dumps(tokens or {}),
        )
        logger.info("Tracking position: %s %s @ %.4f", direction.value, market_id, entry_price)

    async def track_exit(self, market_id: str) -> None:
        self._positions.pop(market_id, None)
        await self._db.delete_position(market_id)

    def get_correlated_exposure(self, category: str) -> float:
        """Sum of exposure for positions matching the given category."""
        if not category:
            return 0.0
        return sum(
            pos.amount for pos in self._positions.values()
            if pos.category == category
        )

    async def load_from_db(self) -> None:
        rows = await self._db.load_positions()
        for row in rows:
            self._positions[row["market_id"]] = TrackedPosition(
                market_id=row["market_id"],
                direction=Direction(row["direction"]),
                entry_price=row["entry_price"],
                amount=row["amount"],
                entry_time=datetime.fromisoformat(row["updated_at"]),
                peak_pnl_pct=row.get("peak_pnl_pct", 0.0),
                tokens=json.loads(row.get("tokens", "{}")),
            )
        logger.info("Loaded %d positions from DB", len(self._positions))

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self) -> None:
        while self._running:
            for market_id, pos in list(self._positions.items()):
                old_peak = pos.peak_pnl_pct
                exit_reason = await self._check_exit(pos)
                if exit_reason:
                    await self._trigger_exit(pos, exit_reason)
                elif pos.peak_pnl_pct > old_peak:
                    await self._db.update_position_peak(pos.market_id, pos.peak_pnl_pct)

            await asyncio.sleep(self._check_interval)

    async def _check_exit(self, pos: TrackedPosition) -> str | None:
        if not self._price_getter:
            return None

        current_price = self._price_getter("polymarket", pos.market_id)
        if current_price is None:
            return None

        # Calculate unrealized P&L percentage
        if pos.direction == Direction.YES:
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - current_price) / pos.entry_price

        # Update peak P&L for trailing stop
        if pnl_pct > pos.peak_pnl_pct:
            pos.peak_pnl_pct = pnl_pct

        # Time-scaled exits: scale TP/SL by days remaining.
        # Near resolution, WIDEN take-profit (ride winners to settlement)
        # but TIGHTEN stop-loss (cut losers faster).
        take_profit = self._rules.take_profit
        stop_loss = self._rules.stop_loss
        if pos.end_date:
            now = datetime.now(timezone.utc)
            days_remaining = max((pos.end_date - now).total_seconds() / 86400, 0.1)
            if days_remaining < 3:
                # Near resolution: ride winners (widen TP), but tighten SL
                take_profit = 0.50  # Let it run to settlement
                stop_loss = max(stop_loss, -0.08)  # Cut losers faster
                logger.debug("Near-resolution exits for %s: TP=%.0f%% SL=%.0f%%",
                            pos.market_id, take_profit * 100, stop_loss * 100)

        # Take profit
        if pnl_pct >= take_profit:
            return f"Take profit: {pnl_pct:+.1%} (target: {take_profit:+.1%})"

        # Stop loss
        if pnl_pct <= stop_loss:
            return f"Stop loss: {pnl_pct:+.1%} (limit: {stop_loss:+.1%})"

        # Trailing stop — activates after gains exceed threshold
        if pos.peak_pnl_pct > self._rules.trailing_stop_activation:
            trailing_trigger = pos.peak_pnl_pct - self._rules.trailing_stop
            if pnl_pct <= trailing_trigger:
                return (f"Trailing stop: {pnl_pct:+.1%} "
                        f"(peak was {pos.peak_pnl_pct:+.1%}, trail: {self._rules.trailing_stop:.0%})")

        # Edge gone — price moved to roughly where we entered
        if pos.direction == Direction.YES:
            edge_remaining = current_price - pos.entry_price
        else:
            edge_remaining = pos.entry_price - current_price

        hours_held = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600
        edge_threshold = self._rules.edge_gone_threshold
        if hours_held > self._rules.time_decay_hours:
            edge_threshold *= 1.5  # Tighten after holding too long

        if abs(edge_remaining) < edge_threshold and hours_held > 1:
            return f"Edge gone: remaining edge {edge_remaining:+.3f} < {edge_threshold:.3f} after {hours_held:.0f}h"

        # Max hold time — only triggers for positions NOT in profit
        if hours_held > self._rules.max_hold_hours and pnl_pct <= 0:
            return f"Max hold time: {hours_held:.0f}h (limit: {self._rules.max_hold_hours}h)"

        return None

    async def _trigger_exit(self, pos: TrackedPosition, reason: str) -> None:
        # Compute pnl_pct for structured logging
        pnl_pct = 0.0
        if self._price_getter:
            current_price = self._price_getter("polymarket", pos.market_id)
            if current_price is not None:
                if pos.direction == Direction.YES:
                    pnl_pct = (current_price - pos.entry_price) / pos.entry_price
                else:
                    pnl_pct = (pos.entry_price - current_price) / pos.entry_price

        hours_held = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600

        current_price_val = self._price_getter("polymarket", pos.market_id) if self._price_getter else None
        logger.info(
            "Exit: %s %s", pos.direction.value, pos.market_id[:16],
            extra={
                "event_type": "exit",
                "market_id": pos.market_id,
                "direction": pos.direction.value,
                "reason": reason,
                "entry_price": pos.entry_price,
                "current_price": current_price_val,
                "amount": pos.amount,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usd": round(pnl_pct * pos.amount, 2),
                "peak_pnl_pct": round(pos.peak_pnl_pct, 4),
                "hours_held": round(hours_held, 1),
                "category": pos.category,
            },
        )

        console.print(
            f"[bold yellow]EXIT[/] {pos.direction.value} {pos.market_id[:20]} — {reason}"
        )

        # Exit = SELL the same direction token (not flip direction)
        decision = TradeDecision(
            market_id=pos.market_id,
            direction=pos.direction,
            amount=pos.amount,
            confidence=0.99,
            signals=[],
            order_type=OrderType.LIMIT,
            tokens=pos.tokens,
            is_exit=True,
        )

        await self._bus.publish("trade_decision", decision)
        await self.track_exit(pos.market_id)
        logger.info("Exit triggered for %s: %s", pos.market_id, reason)
