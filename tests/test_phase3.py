"""Phase 3 tests — circuit breaker, event bus, JSON logging, metrics endpoint."""

import asyncio
import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from polymarket_weather.event_bus import EventBus
from polymarket_weather.logging_filters import JSONLogFormatter
from polymarket_weather.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    all_breakers,
    get_breaker,
)


# ---------------------------------------------------------------------------
# 3.2 Circuit breaker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_breaker_starts_closed():
    br = CircuitBreaker("test1", failure_threshold=3, reset_timeout=0.1)
    assert br.state is CircuitState.CLOSED

    async def ok():
        return "ok"

    result = await br.call(ok)
    assert result == "ok"
    assert br.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_failures():
    br = CircuitBreaker("test2", failure_threshold=3, reset_timeout=0.1)

    async def bad():
        raise RuntimeError("fail")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await br.call(bad)

    assert br.state is CircuitState.OPEN
    # Next call short-circuits without invoking the function
    with pytest.raises(CircuitOpenError):
        await br.call(bad)


@pytest.mark.asyncio
async def test_breaker_half_opens_after_timeout():
    br = CircuitBreaker("test3", failure_threshold=2, reset_timeout=0.05)

    async def bad():
        raise RuntimeError("fail")

    async def good():
        return "recovered"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await br.call(bad)
    assert br.state is CircuitState.OPEN

    await asyncio.sleep(0.06)

    # Successful probe closes the breaker
    result = await br.call(good)
    assert result == "recovered"
    assert br.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_probe_failure_reopens():
    br = CircuitBreaker("test4", failure_threshold=2, reset_timeout=0.05)

    async def bad():
        raise RuntimeError("fail")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await br.call(bad)
    await asyncio.sleep(0.06)

    with pytest.raises(RuntimeError):
        await br.call(bad)
    assert br.state is CircuitState.OPEN


def test_get_breaker_is_idempotent():
    br1 = get_breaker("registry_test")
    br2 = get_breaker("registry_test")
    assert br1 is br2
    assert "registry_test" in all_breakers()


# ---------------------------------------------------------------------------
# 3.5 EventBus surfaces exceptions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eventbus_returns_empty_on_success():
    bus = EventBus()

    async def ok(_e):
        pass

    bus.subscribe("t", ok)
    errors = await bus.publish("t", {})
    assert errors == []


@pytest.mark.asyncio
async def test_eventbus_returns_errors_from_failing_handlers():
    bus = EventBus()

    async def bad(_e):
        raise ValueError("kaboom")

    bus.subscribe("t", bad)
    errors = await bus.publish("t", {})
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


@pytest.mark.asyncio
async def test_eventbus_continues_to_later_handlers_after_failure():
    bus = EventBus()
    calls = []

    async def first(_e):
        raise RuntimeError("first failed")

    async def second(_e):
        calls.append("second")

    bus.subscribe("t", first)
    bus.subscribe("t", second)
    errors = await bus.publish("t", {})

    assert "second" in calls
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# 3.3 JSON log formatter
# ---------------------------------------------------------------------------

def test_json_formatter_produces_valid_json():
    record = logging.LogRecord(
        name="pmw.test", level=logging.INFO, pathname="/x", lineno=1,
        msg="hello world", args=(), exc_info=None,
    )
    formatter = JSONLogFormatter()
    out = formatter.format(record)
    parsed = json.loads(out)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "pmw.test"
    assert parsed["msg"] == "hello world"
    assert "ts" in parsed


def test_json_formatter_scrubs_secrets():
    record = logging.LogRecord(
        name="pmw.test", level=logging.INFO, pathname="/x", lineno=1,
        msg="key=0xf2513f60d55f2ed7b491c6e6f19c004c0728dbed0c36b714b054d7556b97f22b",
        args=(), exc_info=None,
    )
    formatter = JSONLogFormatter()
    out = formatter.format(record)
    parsed = json.loads(out)
    assert "0xf2513f60" not in parsed["msg"]
    assert "REDACTED" in parsed["msg"]


def test_json_formatter_includes_extras():
    record = logging.LogRecord(
        name="pmw.test", level=logging.INFO, pathname="/x", lineno=1,
        msg="trade placed", args=(), exc_info=None,
    )
    record.market_id = "0xabc"
    record.city = "nyc"
    formatter = JSONLogFormatter()
    out = formatter.format(record)
    parsed = json.loads(out)
    assert parsed["market_id"] == "0xabc"
    assert parsed["city"] == "nyc"


# ---------------------------------------------------------------------------
# 3.4 /api/metrics endpoint
# ---------------------------------------------------------------------------

def test_metrics_endpoint_returns_prometheus_format():
    # DASH_PASS set in conftest
    from polymarket_weather.api.dashboard import app

    client = TestClient(app)
    resp = client.get(
        "/api/metrics",
        headers={"X-API-Key": os.environ["DASH_PASS"]},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "pmw_breaker_state" in body
    assert "pmw_paper_mode" in body
    # Ensure Prometheus-ish format: # HELP / # TYPE lines
    assert "# HELP" in body
    assert "# TYPE" in body


def test_metrics_endpoint_requires_auth():
    from polymarket_weather.api.dashboard import app

    client = TestClient(app)
    resp = client.get("/api/metrics")  # no key
    assert resp.status_code == 401
