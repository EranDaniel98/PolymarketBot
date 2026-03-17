import pytest
from unittest.mock import AsyncMock, patch
from polymarket_bot.notifications.discord import DiscordNotifier
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.models import TradeDecision, Direction, OrderType


@pytest.fixture
def notifier():
    return DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/token")


async def test_discord_name(notifier):
    assert notifier.name == "discord"


async def test_send_alert(notifier):
    await notifier.start()
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock) as mock_send:
        await notifier.send_alert("Server restarted", NotificationLevel.INFO)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][0]
        assert payload["embeds"][0]["title"] == "INFO"
        assert "Server restarted" in payload["embeds"][0]["description"]
    await notifier.stop()


async def test_send_trade_notification(notifier):
    await notifier.start()
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock) as mock_send:
        await notifier.send_trade_notification("m1", "YES", 100.0, 0.55)
        mock_send.assert_called_once()
        embed = mock_send.call_args[0][0]["embeds"][0]
        assert embed["title"] == "Trade Executed"
        assert embed["color"] == 3066993
    await notifier.stop()


async def test_request_approval_always_false(notifier):
    await notifier.start()
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock):
        result = await notifier.request_approval(decision)
        assert result is False
    await notifier.stop()
