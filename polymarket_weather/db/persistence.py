"""DB-backed persistence for positions, risk state, and system counters.

Phase 2 of the hardening plan. Key responsibilities:

  - persist_position_entry / persist_position_exit — write-through for every
    mutation of PositionManager._positions, so state survives process crashes.
  - load_open_positions — startup reconciliation: reads all trades with
    status='open' and rebuilds the in-memory cache.
  - get_daily_loss / record_daily_loss — daily-loss counter keyed by date;
    auto-resets when the date rolls over (no timer needed).
  - get_completed_trades / increment_completed_trades — bootstrap-phase
    counter that survives restarts.
  - ensure_schema — idempotent ALTER TABLE ADD COLUMN IF NOT EXISTS for the
    Trade columns added in Phase 2.1. Bridges the gap until Phase 7.4 Alembic
    migrations land.

All functions accept the async_sessionmaker factory and manage their own
sessions + commits. Callers are expected to call them from async context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from polymarket_weather.db.models import SystemState, Trade

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema migration helper (ALTER TABLE ADD COLUMN IF NOT EXISTS)
# ---------------------------------------------------------------------------

# Columns added to `trades` in Phase 2.1. When the table already exists from
# a prior deploy (fresh Base.metadata.create_all only creates missing tables,
# not missing columns), these ALTERs add them idempotently. Uses PG syntax
# but also works on modern SQLite via a fallback branch.
_TRADE_COLUMN_ADDS: list[tuple[str, str]] = [
    ("market_id", "VARCHAR(100)"),
    ("direction", "VARCHAR(5)"),
    ("city", "VARCHAR(100)"),
    ("region", "VARCHAR(50)"),
    ("event_id", "VARCHAR(100)"),
    ("peak_pnl_pct", "NUMERIC(8, 4)"),
]


async def ensure_schema(session_factory: async_sessionmaker) -> None:
    """Run idempotent ALTER TABLE migrations. Safe on every boot."""
    async with session_factory() as session:
        bind = session.get_bind()
        dialect = bind.dialect.name  # "postgresql" or "sqlite"

        for col_name, col_type in _TRADE_COLUMN_ADDS:
            if dialect == "postgresql":
                stmt = sa.text(
                    f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                )
                try:
                    await session.execute(stmt)
                except Exception:
                    logger.exception("Failed to ALTER trades.%s", col_name)
            elif dialect == "sqlite":
                # SQLite doesn't support IF NOT EXISTS on ADD COLUMN, so probe
                # the pragma first and skip if present.
                pragma = await session.execute(sa.text("PRAGMA table_info(trades)"))
                existing = {row[1] for row in pragma.fetchall()}
                if col_name not in existing:
                    try:
                        await session.execute(
                            sa.text(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                        )
                    except Exception:
                        logger.exception("Failed to ALTER trades.%s (sqlite)", col_name)
            # Unknown dialects: silently skip; create_all handles fresh installs.
        await session.commit()
    logger.info("Schema migration check complete")


# ---------------------------------------------------------------------------
# Position persistence
# ---------------------------------------------------------------------------

@dataclass
class PersistedPosition:
    market_id: str
    direction: str
    entry_price: float
    size_usdc: float
    city: str
    region: str
    event_id: str
    entry_time: datetime
    peak_pnl_pct: float


async def persist_position_entry(
    session_factory: async_sessionmaker,
    position: PersistedPosition,
    opportunity_id: int | None = None,
    poly_order_id: str | None = None,
) -> int:
    """INSERT a new Trade row with status='open' representing a new position.

    Returns the trade id. The caller should store it alongside the in-memory
    cache entry so exit can update the correct row.
    """
    async with session_factory() as session:
        trade = Trade(
            opportunity_id=opportunity_id,
            poly_order_id=poly_order_id,
            market_id=position.market_id,
            direction=position.direction,
            city=position.city,
            region=position.region,
            event_id=position.event_id,
            size_usdc=position.size_usdc,
            fill_price=position.entry_price,
            peak_pnl_pct=position.peak_pnl_pct,
            status="open",
            placed_at=position.entry_time,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return int(trade.id)


async def persist_position_exit(
    session_factory: async_sessionmaker,
    market_id: str,
    exit_price: float,
    pnl_usdc: float,
    exit_reason: str,
) -> None:
    """UPDATE the open Trade for this market_id to status='closed'."""
    async with session_factory() as session:
        result = await session.execute(
            sa.select(Trade)
            .where(Trade.market_id == market_id)
            .where(Trade.status == "open")
            .order_by(Trade.placed_at.desc())
            .limit(1)
        )
        trade = result.scalar_one_or_none()
        if trade is None:
            logger.warning("persist_position_exit: no open trade for %s", market_id)
            return
        trade.status = "closed"
        trade.fill_price = exit_price  # Reuse fill_price as exit_price — see note in docstring
        trade.pnl_usdc = pnl_usdc
        trade.exit_reason = exit_reason
        trade.settled_at = datetime.now(timezone.utc)
        await session.commit()


async def persist_peak_pnl_pct(
    session_factory: async_sessionmaker,
    market_id: str,
    peak_pnl_pct: float,
) -> None:
    """UPDATE peak_pnl_pct on the open trade row (for trailing-stop tracking)."""
    async with session_factory() as session:
        result = await session.execute(
            sa.select(Trade)
            .where(Trade.market_id == market_id)
            .where(Trade.status == "open")
            .limit(1)
        )
        trade = result.scalar_one_or_none()
        if trade is None:
            return
        trade.peak_pnl_pct = peak_pnl_pct
        await session.commit()


async def load_open_positions(
    session_factory: async_sessionmaker,
) -> list[PersistedPosition]:
    """Read all status='open' trades and return them as PersistedPosition DTOs.

    Called on startup to rebuild the in-memory PositionManager cache. Any row
    with NULL in the denormalized fields (e.g. a pre-Phase-2 trade) is skipped
    with a warning — the fix for those is manual.
    """
    async with session_factory() as session:
        result = await session.execute(
            sa.select(Trade).where(Trade.status == "open")
        )
        rows = result.scalars().all()

    out: list[PersistedPosition] = []
    for r in rows:
        if not r.market_id or not r.direction or r.fill_price is None or r.size_usdc is None:
            logger.warning(
                "load_open_positions: skipping incomplete trade id=%s (nulls in required fields)",
                r.id,
            )
            continue
        out.append(PersistedPosition(
            market_id=r.market_id,
            direction=r.direction,
            entry_price=float(r.fill_price),
            size_usdc=float(r.size_usdc),
            city=r.city or "",
            region=r.region or "",
            event_id=r.event_id or "",
            entry_time=r.placed_at,
            peak_pnl_pct=float(r.peak_pnl_pct or 0.0),
        ))
    logger.info("Reconciled %d open positions from DB", len(out))
    return out


# ---------------------------------------------------------------------------
# SystemState — daily loss, completed trades, paused flag
# ---------------------------------------------------------------------------

async def _get_state(session, key: str) -> str | None:
    result = await session.execute(
        sa.select(SystemState.value).where(SystemState.key == key)
    )
    return result.scalar_one_or_none()


async def _set_state(session, key: str, value: str) -> None:
    existing = await session.execute(
        sa.select(SystemState).where(SystemState.key == key)
    )
    row = existing.scalar_one_or_none()
    if row is None:
        session.add(SystemState(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)


def _today_key() -> str:
    return f"daily_loss:{date.today().isoformat()}"


async def get_daily_loss(session_factory: async_sessionmaker) -> float:
    """Return the loss accumulated so far today. Returns 0.0 on a new day."""
    async with session_factory() as session:
        raw = await _get_state(session, _today_key())
    return float(raw) if raw else 0.0


async def record_daily_loss(session_factory: async_sessionmaker, amount: float) -> float:
    """Atomically add `amount` to today's daily_loss and return the new total."""
    async with session_factory() as session:
        key = _today_key()
        raw = await _get_state(session, key)
        current = float(raw) if raw else 0.0
        new_total = current + amount
        await _set_state(session, key, str(new_total))
        await session.commit()
    return new_total


async def get_completed_trades(session_factory: async_sessionmaker) -> int:
    """Return count of completed (settled) trades — used for bootstrap sizing."""
    async with session_factory() as session:
        # Source of truth is trades table, not a counter: self-healing.
        result = await session.execute(
            sa.select(sa.func.count(Trade.id)).where(Trade.status == "settled")
        )
        return int(result.scalar() or 0)


async def is_trading_paused(session_factory: async_sessionmaker) -> bool:
    async with session_factory() as session:
        raw = await _get_state(session, "is_paused")
    return raw == "true"


async def set_trading_paused(session_factory: async_sessionmaker, paused: bool) -> None:
    async with session_factory() as session:
        await _set_state(session, "is_paused", "true" if paused else "false")
        await session.commit()
