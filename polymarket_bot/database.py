import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from polymarket_bot.models import Signal, TradeExecution

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    order_id TEXT,
    status TEXT NOT NULL,
    fees REAL DEFAULT 0.0,
    realized_pnl REAL DEFAULT 0.0,
    error TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    end_date TEXT,
    tokens TEXT,
    current_price REAL,
    category TEXT DEFAULT '',
    correlation_tags TEXT DEFAULT '[]',
    platform_mappings TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS portfolio (
    market_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def _write(self, sql: str, params: tuple = ()) -> None:
        async with self._write_lock:
            await self._db.execute(sql, params)
            await self._db.commit()

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_tables(self) -> list[str]:
        rows = await self._fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [r["name"] for r in rows]

    async def save_signal(self, signal: Signal) -> None:
        await self._write(
            "INSERT INTO signals (source, market_id, direction, confidence, reasoning, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal.source, signal.market_id, signal.direction.value,
             signal.confidence, signal.reasoning, signal.timestamp.isoformat()),
        )

    async def get_signals(self, market_id: str, since_minutes: int = 60) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
        return await self._fetch_all(
            "SELECT * FROM signals WHERE market_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (market_id, since),
        )

    async def save_trade(self, trade: TradeExecution) -> None:
        await self._write(
            "INSERT INTO trades (market_id, direction, amount, price, order_id, status, fees, realized_pnl, error, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade.market_id, trade.direction.value, trade.amount, trade.price,
             trade.order_id, trade.status.value, trade.fees, trade.realized_pnl,
             trade.error, trade.timestamp.isoformat()),
        )

    async def get_trades(self, market_id: str | None = None) -> list[dict]:
        if market_id:
            return await self._fetch_all(
                "SELECT * FROM trades WHERE market_id = ? ORDER BY timestamp DESC", (market_id,)
            )
        return await self._fetch_all("SELECT * FROM trades ORDER BY timestamp DESC")

    async def get_daily_pnl(self) -> float:
        """Sum realized P&L for today from the explicit realized_pnl column."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(realized_pnl), 0) as pnl "
            "FROM trades WHERE timestamp >= ?", (today,)
        )
        return row["pnl"] if row else 0.0

    async def get_total_exposure(self) -> float:
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(amount), 0) as total FROM portfolio"
        )
        return row["total"] if row else 0.0

    async def get_trade_count(self) -> int:
        row = await self._fetch_one("SELECT COUNT(*) as cnt FROM trades")
        return row["cnt"] if row else 0

    async def save_price(self, platform: str, market_id: str, price: float) -> None:
        await self._write(
            "INSERT INTO prices (platform, market_id, price, timestamp) VALUES (?, ?, ?, ?)",
            (platform, market_id, price, datetime.now(timezone.utc).isoformat()),
        )
