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
    peak_pnl_pct REAL DEFAULT 0.0,
    tokens TEXT DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market_id TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    market_price_at_signal REAL,
    actual_outcome TEXT,
    was_correct INTEGER,
    signal_timestamp TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS market_resolutions (
    market_id TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    signal_source TEXT NOT NULL,
    signal_direction TEXT NOT NULL,
    signal_confidence REAL,
    market_price_at_signal REAL,
    timestamp TEXT NOT NULL
);
"""

MIGRATIONS = [
    # Add peak_pnl_pct and tokens columns if missing (idempotent)
    "ALTER TABLE portfolio ADD COLUMN peak_pnl_pct REAL DEFAULT 0.0",
    "ALTER TABLE portfolio ADD COLUMN tokens TEXT DEFAULT '{}'",
    "ALTER TABLE portfolio ADD COLUMN end_date TEXT",
    "ALTER TABLE portfolio ADD COLUMN category TEXT DEFAULT ''",
]


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
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        for sql in MIGRATIONS:
            try:
                await self._db.execute(sql)
                await self._db.commit()
            except Exception:
                pass  # Column already exists

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

    # --- Signals ---

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

    # --- Trades ---

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

    async def get_daily_trades(self) -> list[dict]:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
        return await self._fetch_all(
            "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC", (today,)
        )

    async def get_total_pnl(self) -> float:
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(realized_pnl), 0) as pnl FROM trades"
        )
        return row["pnl"] if row else 0.0

    async def get_win_rate(self) -> float:
        row = await self._fetch_one(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins "
            "FROM trades WHERE status = 'filled' AND realized_pnl != 0"
        )
        if not row or row["total"] == 0:
            return 0.0
        return row["wins"] / row["total"]

    # --- Prices ---

    async def save_price(self, platform: str, market_id: str, price: float) -> None:
        await self._write(
            "INSERT INTO prices (platform, market_id, price, timestamp) VALUES (?, ?, ?, ?)",
            (platform, market_id, price, datetime.now(timezone.utc).isoformat()),
        )

    # --- Portfolio (Position Persistence) ---

    async def save_position(self, market_id: str, direction: str, amount: float,
                            entry_price: float, peak_pnl_pct: float = 0.0,
                            tokens: str = "{}",
                            end_date: str | None = None,
                            category: str = "") -> None:
        await self._write(
            "INSERT OR REPLACE INTO portfolio "
            "(market_id, direction, amount, entry_price, peak_pnl_pct, tokens, "
            "end_date, category, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (market_id, direction, amount, entry_price, peak_pnl_pct, tokens,
             end_date, category, datetime.now(timezone.utc).isoformat()),
        )

    async def load_positions(self) -> list[dict]:
        return await self._fetch_all("SELECT * FROM portfolio")

    async def delete_position(self, market_id: str) -> None:
        await self._write("DELETE FROM portfolio WHERE market_id = ?", (market_id,))

    async def update_position_peak(self, market_id: str, peak_pnl_pct: float) -> None:
        await self._write(
            "UPDATE portfolio SET peak_pnl_pct = ?, updated_at = ? WHERE market_id = ?",
            (peak_pnl_pct, datetime.now(timezone.utc).isoformat(), market_id),
        )

    # --- Signal Outcomes (Accuracy Tracking) ---

    async def save_signal_outcome(
        self, source: str, market_id: str, predicted_direction: str,
        confidence: float, market_price: float | None, timestamp: datetime,
    ) -> None:
        await self._write(
            "INSERT INTO signal_outcomes "
            "(source, market_id, predicted_direction, confidence, market_price_at_signal, signal_timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source, market_id, predicted_direction, confidence, market_price, timestamp.isoformat()),
        )

    async def record_resolution(self, market_id: str, outcome: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._write_lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO market_resolutions (market_id, outcome, resolved_at) "
                "VALUES (?, ?, ?)",
                (market_id, outcome, now),
            )
            # Backfill signal_outcomes for this market
            await self._db.execute(
                "UPDATE signal_outcomes SET actual_outcome = ?, resolved_at = ?, "
                "was_correct = CASE "
                "  WHEN (predicted_direction = 'YES' AND ? = 'Yes') THEN 1 "
                "  WHEN (predicted_direction = 'NO' AND ? = 'No') THEN 1 "
                "  ELSE 0 "
                "END "
                "WHERE market_id = ? AND actual_outcome IS NULL",
                (outcome, now, outcome, outcome, market_id),
            )
            await self._db.commit()

    async def get_signal_accuracy(self, source: str, min_signals: int = 10) -> dict | None:
        row = await self._fetch_one(
            "SELECT COUNT(*) as n, "
            "SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct, "
            "AVG(confidence) as avg_conf "
            "FROM signal_outcomes WHERE source = ? AND was_correct IS NOT NULL",
            (source,),
        )
        if not row or row["n"] < min_signals:
            return None
        return {
            "accuracy": row["correct"] / row["n"],
            "n_signals": row["n"],
            "avg_confidence": row["avg_conf"],
        }

    async def get_accuracy_report(self) -> dict[str, dict]:
        rows = await self._fetch_all(
            "SELECT source, COUNT(*) as n, "
            "SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct, "
            "AVG(confidence) as avg_conf "
            "FROM signal_outcomes WHERE was_correct IS NOT NULL "
            "GROUP BY source"
        )
        report = {}
        for row in rows:
            report[row["source"]] = {
                "accuracy": row["correct"] / row["n"] if row["n"] > 0 else 0,
                "n_signals": row["n"],
                "avg_confidence": row["avg_conf"],
            }
        return report

    # --- Trade-Signal Linkage ---

    async def save_trade_signals(self, trade_id: str, signals: list) -> None:
        """Save the signals that contributed to a trade decision."""
        for sig in signals:
            await self._write(
                "INSERT INTO trade_signals "
                "(trade_id, signal_source, signal_direction, signal_confidence, "
                "market_price_at_signal, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (trade_id, sig.source, sig.direction.value, sig.confidence,
                 None, sig.timestamp.isoformat()),
            )

    async def get_win_rate_by_signal(self) -> dict[str, dict]:
        """Get win rate grouped by signal source for trades that have resolved."""
        rows = await self._fetch_all(
            "SELECT ts.signal_source, "
            "COUNT(DISTINCT ts.trade_id) as n_trades, "
            "SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as wins "
            "FROM trade_signals ts "
            "JOIN trades t ON ts.trade_id = t.order_id "
            "WHERE t.status = 'filled' AND t.realized_pnl != 0 "
            "GROUP BY ts.signal_source"
        )
        report = {}
        for row in rows:
            n = row["n_trades"]
            report[row["signal_source"]] = {
                "win_rate": row["wins"] / n if n > 0 else 0,
                "n_trades": n,
            }
        return report

    async def get_confidence_calibration(
        self, source: str, bucket_size: float = 0.10,
    ) -> list[dict]:
        """Bin resolved signals by confidence and compute actual accuracy per bucket."""
        rows = await self._fetch_all(
            "SELECT confidence, was_correct FROM signal_outcomes "
            "WHERE source = ? AND was_correct IS NOT NULL",
            (source,),
        )
        if not rows:
            return []
        from collections import defaultdict
        buckets = defaultdict(lambda: {"correct": 0, "total": 0})
        for row in rows:
            conf = row["confidence"]
            bucket_idx = int(round(conf / bucket_size, 8))
            bucket_min = round(bucket_idx * bucket_size, 2)
            buckets[bucket_min]["total"] += 1
            if row["was_correct"]:
                buckets[bucket_min]["correct"] += 1
        result = []
        for bucket_min in sorted(buckets):
            b = buckets[bucket_min]
            result.append({
                "bucket_min": bucket_min,
                "bucket_max": round(bucket_min + bucket_size, 2),
                "total": b["total"],
                "correct": b["correct"],
                "accuracy": b["correct"] / b["total"] if b["total"] > 0 else 0,
            })
        return result

    async def get_confidence_gap(self, source: str) -> dict | None:
        """Compute the gap between average predicted confidence and actual accuracy."""
        row = await self._fetch_one(
            "SELECT AVG(confidence) as avg_conf, "
            "AVG(CASE WHEN was_correct = 1 THEN 1.0 ELSE 0.0 END) as accuracy, "
            "COUNT(*) as n "
            "FROM signal_outcomes WHERE source = ? AND was_correct IS NOT NULL",
            (source,),
        )
        if not row or row["n"] == 0:
            return None
        avg_conf = row["avg_conf"]
        accuracy = row["accuracy"]
        return {
            "avg_confidence": avg_conf,
            "actual_accuracy": accuracy,
            "gap": avg_conf - accuracy,
            "n_signals": row["n"],
        }

    async def get_fee_impact_report(self) -> dict:
        """Compute total fees as fraction of trading volume."""
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(fees), 0) as total_fees, "
            "COALESCE(SUM(realized_pnl), 0) as total_pnl, "
            "COALESCE(SUM(amount), 0) as total_volume, "
            "COUNT(*) as n_trades "
            "FROM trades WHERE status = 'filled'"
        )
        total_vol = row["total_volume"] if row["total_volume"] else 1
        return {
            "total_fees": row["total_fees"],
            "total_pnl": row["total_pnl"],
            "total_volume": row["total_volume"],
            "n_trades": row["n_trades"],
            "fee_pct_of_volume": row["total_fees"] / total_vol if total_vol > 0 else 0,
        }

    async def get_untracked_trades(self) -> list[dict]:
        """Find filled BUY trades with no matching portfolio entry."""
        return await self._fetch_all(
            "SELECT t.market_id, t.direction, t.amount, t.price, t.timestamp "
            "FROM trades t "
            "LEFT JOIN portfolio p ON t.market_id = p.market_id "
            "WHERE t.status = 'filled' AND p.market_id IS NULL "
            "AND t.order_id NOT LIKE '%_exit_%' "
            "ORDER BY t.timestamp DESC"
        )

    async def get_unresolved_market_ids(self) -> list[str]:
        rows = await self._fetch_all(
            "SELECT DISTINCT so.market_id FROM signal_outcomes so "
            "LEFT JOIN market_resolutions mr ON so.market_id = mr.market_id "
            "WHERE mr.market_id IS NULL"
        )
        return [r["market_id"] for r in rows]
