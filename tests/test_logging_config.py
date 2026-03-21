import json
import logging
import pytest
from pathlib import Path
from polymarket_bot.logging_config import setup_file_logging, StructuredLogger


def test_file_handler_writes_jsonl(tmp_path):
    log_file = tmp_path / "bot.jsonl"
    handler = setup_file_logging(log_file)
    logger = logging.getLogger("test_jsonl")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("test message", extra={"event_type": "test"})
    handler.flush()

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == "test message"
    assert record["event_type"] == "test"
    assert "timestamp" in record
    assert "level" in record


def test_structured_logger_signal():
    slog = StructuredLogger("test")
    extra = slog.signal_event(
        source="llm", market_id="m1", direction="YES",
        confidence=0.75, market_price=0.50,
    )
    assert extra["event_type"] == "signal"
    assert extra["source"] == "llm"
    assert extra["confidence"] == 0.75


def test_structured_logger_trade_decision():
    slog = StructuredLogger("test")
    extra = slog.trade_decision(
        market_id="m1", direction="YES", amount=25.0,
        confidence=0.85, action="auto_execute", signals=["llm", "whale"],
    )
    assert extra["event_type"] == "trade_decision"
    assert extra["action"] == "auto_execute"
    assert extra["signals"] == ["llm", "whale"]


def test_structured_logger_risk_check():
    slog = StructuredLogger("test")
    extra = slog.risk_check(
        market_id="m1", approved=False, reason="Circuit breaker",
        amount=25.0, edge=0.03,
    )
    assert extra["event_type"] == "risk_check"
    assert extra["approved"] is False


def test_structured_logger_exit():
    slog = StructuredLogger("test")
    extra = slog.exit_event(
        market_id="m1", reason="Take profit", pnl_pct=0.22,
        hours_held=8.5, direction="YES",
    )
    assert extra["event_type"] == "exit"
    assert extra["pnl_pct"] == 0.22
