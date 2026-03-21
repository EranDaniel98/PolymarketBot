"""Structured JSON-lines file logging for post-hoc analysis."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    # Standard LogRecord fields to exclude from extras
    _BUILTIN = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Capture ALL custom extra fields dynamically
        for key, val in record.__dict__.items():
            if key not in self._BUILTIN and val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


def setup_file_logging(
    path: Path,
    level: int = logging.INFO,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """Create a rotating JSON-lines file handler."""
    from logging.handlers import RotatingFileHandler

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    return handler


class StructuredLogger:
    """Helper to emit structured log events with typed extras."""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def signal_event(self, source: str, market_id: str, direction: str,
                     confidence: float, market_price: float) -> dict:
        extra = {
            "event_type": "signal", "source": source,
            "market_id": market_id, "direction": direction,
            "confidence": confidence, "market_price": market_price,
        }
        self._logger.info(
            "Signal: %s %s conf=%.2f", source, direction, confidence,
            extra=extra,
        )
        return extra

    def trade_decision(self, market_id: str, direction: str, amount: float,
                       confidence: float, action: str,
                       signals: list[str]) -> dict:
        extra = {
            "event_type": "trade_decision", "market_id": market_id,
            "direction": direction, "amount": amount,
            "confidence": confidence, "action": action, "signals": signals,
        }
        self._logger.info(
            "Decision: %s %s $%.2f (%s)", direction, market_id[:16], amount, action,
            extra=extra,
        )
        return extra

    def risk_check(self, market_id: str, approved: bool, reason: str,
                   amount: float, edge: float) -> dict:
        extra = {
            "event_type": "risk_check", "market_id": market_id,
            "approved": approved, "reason": reason,
            "amount": amount, "edge": edge,
        }
        level = logging.INFO if approved else logging.WARNING
        self._logger.log(level, "Risk: %s \u2014 %s", market_id[:16], reason, extra=extra)
        return extra

    def exit_event(self, market_id: str, reason: str, pnl_pct: float,
                   hours_held: float, direction: str) -> dict:
        extra = {
            "event_type": "exit", "market_id": market_id,
            "reason": reason, "pnl_pct": pnl_pct,
            "hours_held": hours_held, "direction": direction,
        }
        self._logger.info(
            "Exit: %s %s pnl=%.1f%%", direction, market_id[:16], pnl_pct * 100,
            extra=extra,
        )
        return extra
