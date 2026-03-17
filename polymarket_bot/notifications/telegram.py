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
        self._pending_approvals: dict[str, asyncio.Future] = {}

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        try:
            from telegram import Bot
            self._bot = Bot(token=self._bot_token)
            logger.info("Telegram notifier started")
        except Exception:
            logger.exception("Failed to start Telegram bot")

    async def stop(self) -> None:
        self._bot = None

    async def _send_message(self, text: str, parse_mode: str = "HTML") -> None:
        if not self._bot:
            logger.warning("Telegram bot not initialized — message: %s", text[:100])
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode=parse_mode,
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

    async def _send_approval_message(self, decision: TradeDecision) -> None:
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
            f"Reply YES to approve, NO to reject.\n"
            f"Auto-cancels in {self._approval_timeout}s."
        )
        await self._send_message(text)

    async def _wait_for_response(self, market_id: str) -> bool | None:
        # TODO v2: Wire up telegram.ext.Application with CallbackQueryHandler
        # for inline button approval. For v1, approvals always time out
        # (auto-cancel), which is the safe default per the spec's timeout design.
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
