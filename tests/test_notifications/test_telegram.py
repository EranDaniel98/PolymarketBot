import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from polymarket_bot.notifications.telegram import TelegramNotifier
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.models import TradeDecision, Direction, OrderType


@pytest.fixture
def notifier():
    return TelegramNotifier(bot_token="test-token", chat_id="12345", approval_timeout=5)


async def test_notifier_name(notifier):
    assert notifier.name == "telegram"


async def test_send_alert(notifier):
    with patch.object(notifier, "_send_message", new_callable=AsyncMock) as mock_send:
        await notifier.send_alert("Test alert", NotificationLevel.INFO)
        mock_send.assert_called_once()
        call_text = mock_send.call_args[0][0]
        assert "Test alert" in call_text


async def test_send_trade_notification(notifier):
    with patch.object(notifier, "_send_message", new_callable=AsyncMock) as mock_send:
        await notifier.send_trade_notification(
            market_id="m1", direction="YES", amount=100.0, price=0.55,
        )
        mock_send.assert_called_once()


async def test_request_approval_timeout(notifier):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_approval_message", new_callable=AsyncMock):
        with patch.object(notifier, "_wait_for_response", new_callable=AsyncMock, return_value=None):
            result = await notifier.request_approval(decision)
            assert result is False


async def test_request_approval_approved(notifier):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_approval_message", new_callable=AsyncMock):
        with patch.object(notifier, "_wait_for_response", new_callable=AsyncMock, return_value=True):
            result = await notifier.request_approval(decision)
            assert result is True
