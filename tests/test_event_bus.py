import pytest
import asyncio
from polymarket_bot.event_bus import EventBus


@pytest.fixture
def bus():
    return EventBus()


async def test_subscribe_and_publish(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("test_event", handler)
    await bus.publish("test_event", {"data": "hello"})
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0]["data"] == "hello"


async def test_multiple_subscribers(bus):
    received_a = []
    received_b = []

    async def handler_a(event):
        received_a.append(event)

    async def handler_b(event):
        received_b.append(event)

    bus.subscribe("evt", handler_a)
    bus.subscribe("evt", handler_b)
    await bus.publish("evt", "payload")
    await asyncio.sleep(0.05)
    assert len(received_a) == 1
    assert len(received_b) == 1


async def test_unsubscribe(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    await bus.publish("evt", "payload")
    await asyncio.sleep(0.05)
    assert len(received) == 0


async def test_publish_no_subscribers(bus):
    # Should not raise
    await bus.publish("nobody_listening", "data")


async def test_handler_error_does_not_break_others(bus):
    received = []

    async def bad_handler(event):
        raise ValueError("boom")

    async def good_handler(event):
        received.append(event)

    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    await bus.publish("evt", "data")
    await asyncio.sleep(0.05)
    assert len(received) == 1
