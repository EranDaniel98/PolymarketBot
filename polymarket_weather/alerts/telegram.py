"""Telegram alerts — adapted from existing bot for weather-specific notifications."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


LEVEL_EMOJI = {
    AlertLevel.INFO: "\u2139\ufe0f",
    AlertLevel.WARNING: "\u26a0\ufe0f",
    AlertLevel.URGENT: "\U0001f6a8",
}


def format_opportunity_message(
    city: str, question: str, our_p: float, market_p: float,
    edge: float, source: str,
) -> str:
    arrow = "\U0001f4c8" if edge > 0 else "\U0001f4c9"
    return (
        f"{arrow} <b>Opportunity Found</b>\n\n"
        f"City: <b>{city}</b>\n"
        f"Market: <b>{question[:80]}</b>\n"
        f"Our P: <b>{our_p:.0%}</b> vs Market: <b>{market_p:.0%}</b>\n"
        f"Edge: <b>{edge:+.2f}</b>\n"
        f"Source: {source}"
    )


def format_trade_message(
    market_id: str, direction: str, amount: float, price: float,
    question: str = "",
) -> str:
    arrow = "\u2b06\ufe0f" if direction == "YES" else "\u2b07\ufe0f"
    market_label = question[:80] if question else market_id[:20]
    return (
        f"{arrow} <b>Trade Executed</b>\n\n"
        f"Market: <b>{market_label}</b>\n"
        f"Direction: <b>{direction}</b>\n"
        f"Amount: <b>${amount:.2f}</b>\n"
        f"Price: <b>${price:.4f}</b>"
    )


def format_settlement_message(
    question: str, outcome: str, pnl: float,
) -> str:
    emoji = "\u2705" if pnl >= 0 else "\u274c"
    return (
        f"{emoji} <b>Market Settled</b>\n\n"
        f"Market: <b>{question[:80]}</b>\n"
        f"Outcome: <b>{outcome}</b>\n"
        f"PnL: <b>{'+' if pnl >= 0 else '-'}${abs(pnl):.2f}</b>"
    )


def format_stale_station_message(station_id: str, hours_ago: float) -> str:
    return (
        f"\u26a0\ufe0f <b>Stale Station</b>\n\n"
        f"Station <b>{station_id}</b> last reported <b>{hours_ago:.1f}h ago</b>.\n"
        f"Trading paused for affected markets."
    )


def format_daily_report(stats: dict) -> str:
    pnl_emoji = "\U0001f4c8" if stats.get("daily_pnl", 0) >= 0 else "\U0001f4c9"
    return (
        f"{pnl_emoji} <b>Daily Report</b>\n\n"
        f"Daily P&L: <b>{'+' if stats.get('daily_pnl', 0) >= 0 else '-'}${abs(stats.get('daily_pnl', 0)):.2f}</b>\n"
        f"Total P&L: <b>{'+' if stats.get('total_pnl', 0) >= 0 else '-'}${abs(stats.get('total_pnl', 0)):.2f}</b>\n"
        f"Trades Today: {stats.get('trade_count', 0)}\n"
        f"Win Rate: {stats.get('win_rate', 0):.0%}\n"
        f"Open Positions: {stats.get('open_positions', 0)}\n"
        f"Bankroll: <b>${stats.get('bankroll', 0):.2f}</b>"
    )


class WeatherTelegramNotifier:
    """Telegram bot for weather arbitrage alerts."""

    def __init__(self, bot_token: str, chat_id: str, alert_on: dict[str, bool] | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._alert_on = alert_on or {
            "opportunity_found": True, "trade_placed": True, "trade_settled": True,
            "risk_limit_approached": True, "data_stale": True, "system_error": True,
        }
        self._bot = None
        self._app = None

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        if not self._bot_token:
            logger.info("Telegram notifier disabled (no bot token)")
            return
        try:
            from telegram.ext import ApplicationBuilder
            self._app = ApplicationBuilder().token(self._bot_token).build()
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            self._bot = self._app.bot
            logger.info("Telegram notifier started")
        except Exception:
            logger.exception("Failed to start Telegram bot")

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.debug("Telegram shutdown error (non-critical)")
        self._bot = None
        self._app = None

    async def _send(self, text: str) -> None:
        if not self._bot:
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def send_alert(self, message: str, level: AlertLevel = AlertLevel.INFO) -> None:
        if not self._alert_on.get("system_error", True) and level == AlertLevel.URGENT:
            return
        emoji = LEVEL_EMOJI.get(level, "")
        await self._send(f"{emoji} <b>{level.value.upper()}</b>\n\n{message}")

    async def send_opportunity(self, city: str, question: str, our_p: float,
                                market_p: float, edge: float, source: str) -> None:
        if not self._alert_on.get("opportunity_found", True):
            return
        await self._send(format_opportunity_message(city, question, our_p, market_p, edge, source))

    async def send_trade(self, market_id: str, direction: str, amount: float,
                          price: float, question: str = "") -> None:
        if not self._alert_on.get("trade_placed", True):
            return
        await self._send(format_trade_message(market_id, direction, amount, price, question))

    async def send_settlement(self, question: str, outcome: str, pnl: float) -> None:
        if not self._alert_on.get("trade_settled", True):
            return
        await self._send(format_settlement_message(question, outcome, pnl))

    async def send_stale_station(self, station_id: str, hours_ago: float) -> None:
        if not self._alert_on.get("data_stale", True):
            return
        await self._send(format_stale_station_message(station_id, hours_ago))

    async def send_daily_report(self, stats: dict) -> None:
        await self._send(format_daily_report(stats))
