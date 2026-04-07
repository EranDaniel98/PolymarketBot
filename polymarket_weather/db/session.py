"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine = None
_session_factory = None


def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory.

    Must be called before using get_session_factory() or get_engine().

    Pool sizing kwargs (pool_size, max_overflow, pool_recycle) are only
    applied for non-SQLite URLs because SQLite uses a StaticPool that does
    not accept those arguments.
    """
    global _engine, _session_factory
    is_sqlite = database_url.startswith("sqlite")
    engine_kwargs: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        engine_kwargs.update(pool_size=10, max_overflow=5, pool_recycle=1800)
    _engine = create_async_engine(database_url, **engine_kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory.  Raises if init_db() has not been called."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory


def get_engine():
    """Return the async engine.  Raises if init_db() has not been called."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


async def dispose_db() -> None:
    """Dispose the engine and reset module-level state."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None
