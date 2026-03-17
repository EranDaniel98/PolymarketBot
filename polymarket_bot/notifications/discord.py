import logging

import httpx

from polymarket_bot.models import TradeDecision
from polymarket_bot.notifications.base import Notifier, NotificationLevel

logger = logging.getLogger(__name__)

LEVEL_COLOR = {
    NotificationLevel.INFO: 3447003,
    NotificationLevel.WARNING: 16776960,
    NotificationLevel.URGENT: 15158332,
}


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "discord"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def send_alert(self, message: str, level: NotificationLevel) -> None:
        embed = {
            "title": level.value.upper(),
            "description": message,
            "color": LEVEL_COLOR.get(level, 0),
        }
        await self._send_webhook({"embeds": [embed]})

    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None:
        embed = {
            "title": "Trade Executed",
            "color": 3066993 if direction == "YES" else 15158332,
            "fields": [
                {"name": "Market", "value": market_id, "inline": True},
                {"name": "Direction", "value": direction, "inline": True},
                {"name": "Amount", "value": f"${amount:.2f}", "inline": True},
                {"name": "Price", "value": f"${price:.4f}", "inline": True},
            ],
        }
        await self._send_webhook({"embeds": [embed]})

    async def request_approval(self, decision: TradeDecision) -> bool:
        await self.send_alert(
            f"Approval needed: {decision.direction.value} {decision.market_id} "
            f"${decision.amount:.2f} (confidence: {decision.confidence:.0%})",
            NotificationLevel.WARNING,
        )
        return False

    async def _send_webhook(self, payload: dict) -> None:
        if not self._client or not self._webhook_url:
            return
        try:
            resp = await self._client.post(self._webhook_url, json=payload)
            resp.raise_for_status()
        except Exception:
            logger.exception("Failed to send Discord webhook")
