import asyncio
import logging

from polymarket_bot.models import TradeDecision
from polymarket_bot.notifications.base import Notifier, NotificationLevel

logger = logging.getLogger(__name__)

LEVEL_EMOJI = {
    NotificationLevel.INFO: "\u2139\ufe0f",
    NotificationLevel.WARNING: "\u26a0\ufe0f",
    NotificationLevel.URGENT: "\U0001f6a8",
}


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, approval_timeout: int = 300):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._approval_timeout = approval_timeout
        self._bot = None
        self._app = None
        self._pending_approvals: dict[str, asyncio.Future] = {}

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        try:
            from telegram.ext import ApplicationBuilder, CallbackQueryHandler

            self._app = ApplicationBuilder().token(self._bot_token).build()
            self._app.add_handler(CallbackQueryHandler(self._on_callback))
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            self._bot = self._app.bot
            logger.info("Telegram notifier started with inline buttons")
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

    async def _on_callback(self, update, context) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return
        action, market_id = parts
        if action == "approve":
            self.resolve_approval(market_id, True)
            await query.answer("Trade approved!")
            await query.edit_message_reply_markup(reply_markup=None)
        elif action == "reject":
            self.resolve_approval(market_id, False)
            await query.answer("Trade rejected.")
            await query.edit_message_reply_markup(reply_markup=None)

    async def _send_message(self, text: str, parse_mode: str = "HTML",
                            reply_markup=None) -> None:
        if not self._bot:
            logger.warning("Telegram bot not initialized — message: %s", text[:100])
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def send_alert(self, message: str, level: NotificationLevel) -> None:
        emoji = LEVEL_EMOJI.get(level, "")
        text = f"{emoji} <b>{level.value.upper()}</b>\n\n{message}"
        await self._send_message(text)

    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None:
        arrow = "\u2b06\ufe0f" if direction == "YES" else "\u2b07\ufe0f"
        text = (
            f"{arrow} <b>Trade Executed</b>\n\n"
            f"Market: <code>{market_id}</code>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Price: <b>${price:.4f}</b>"
        )
        await self._send_message(text)

    async def send_daily_report(self, stats: dict) -> None:
        pnl_emoji = "\U0001f4c8" if stats["daily_pnl"] >= 0 else "\U0001f4c9"
        text = (
            f"{pnl_emoji} <b>Daily Report</b>\n\n"
            f"Daily P&L: <b>${stats['daily_pnl']:+.2f}</b>\n"
            f"Total P&L: <b>${stats['total_pnl']:+.2f}</b>\n"
            f"Trades Today: {stats['trade_count']}\n"
            f"Win Rate: {stats['win_rate']:.0%}\n"
            f"Open Positions: {stats['open_positions']}\n"
            f"Bankroll: <b>${stats['bankroll']:.2f}</b>"
        )
        await self._send_message(text)

    async def _send_approval_message(self, decision: TradeDecision) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        signal_summary = ", ".join(
            f"{s.source}({s.confidence:.0%})" for s in decision.signals[:5]
        )
        text = (
            f"\U0001f4cb <b>Approval Required</b>\n\n"
            f"Market: <code>{decision.market_id}</code>\n"
            f"Direction: <b>{decision.direction.value}</b>\n"
            f"Amount: <b>${decision.amount:.2f}</b>\n"
            f"Confidence: <b>{decision.confidence:.0%}</b>\n"
            f"Signals: {signal_summary or 'N/A'}\n\n"
            f"Auto-cancels in {self._approval_timeout}s."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{decision.market_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject:{decision.market_id}"),
            ]
        ])
        await self._send_message(text, reply_markup=keyboard)

    async def _wait_for_response(self, market_id: str) -> bool | None:
        try:
            future = asyncio.get_running_loop().create_future()
            self._pending_approvals[market_id] = future
            result = await asyncio.wait_for(future, timeout=self._approval_timeout)
            return result
        except asyncio.TimeoutError:
            logger.info("Approval timeout for market %s", market_id)
            return None
        finally:
            self._pending_approvals.pop(market_id, None)

    async def request_approval(self, decision: TradeDecision) -> bool:
        await self._send_approval_message(decision)
        response = await self._wait_for_response(decision.market_id)
        if response is None:
            await self.send_alert(
                f"Approval expired for {decision.market_id} — trade cancelled.",
                NotificationLevel.WARNING,
            )
            return False
        return response

    def resolve_approval(self, market_id: str, approved: bool) -> None:
        future = self._pending_approvals.get(market_id)
        if future and not future.done():
            future.set_result(approved)
