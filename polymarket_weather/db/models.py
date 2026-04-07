"""SQLAlchemy 2.0 ORM models for all 10 database tables."""

from __future__ import annotations

from datetime import datetime

from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class _BigIntPK(TypeDecorator):
    """Maps to BIGINT on PostgreSQL and INTEGER on SQLite.

    SQLite requires the primary key column type to be exactly INTEGER (not
    BIGINT) for the rowid/autoincrement mechanism to work.  This decorator
    transparently uses BigInteger on PostgreSQL and Integer on SQLite so the
    tests can run against an in-memory SQLite database.
    """

    impl = BigInteger
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Integer())
        return dialect.type_descriptor(BigInteger())


class Base(AsyncAttrs, DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Table 1: IcaoStation
# ---------------------------------------------------------------------------

class IcaoStation(Base):
    __tablename__ = "icao_stations"

    station_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    city_name: Mapped[str] = mapped_column(String(100), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    lat: Mapped[float] = mapped_column(Numeric(8, 5))
    lon: Mapped[float] = mapped_column(Numeric(8, 5))
    elevation_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_report_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reliability_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )


# ---------------------------------------------------------------------------
# Table 2: MetarReading
# ---------------------------------------------------------------------------

class MetarReading(Base):
    __tablename__ = "metar_readings"
    __table_args__ = (
        UniqueConstraint("station_id", "observed_at", name="uq_metar_station_obs"),
        Index("ix_metar_station_observed", "station_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(
        String(10), sa.ForeignKey("icao_stations.station_id")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    dewp: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    altim: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    wspd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wdir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wgst: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visib: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cloud_cover: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    wx_string: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slp_hpa: Mapped[float | None] = mapped_column(Numeric(6, 1), nullable=True)
    metar_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    temp_precise_c: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    raw_metar: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Table 3: PolyMarket
# ---------------------------------------------------------------------------

class PolyMarket(Base):
    __tablename__ = "poly_markets"
    __table_args__ = (
        Index(
            "ix_poly_markets_active",
            "status",
            postgresql_where=sa.text("status = 'active'"),
        ),
    )

    market_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    city_name: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    station_id: Mapped[str | None] = mapped_column(
        String(10), sa.ForeignKey("icao_stations.station_id"), nullable=True
    )
    metric: Mapped[str | None] = mapped_column(String(20), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    threshold_upper: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(5), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    resolution_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    yes_token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    no_token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    resolution_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_price: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)


# ---------------------------------------------------------------------------
# Table 4: Opportunity
# ---------------------------------------------------------------------------

class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_market_detected", "market_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String(100), sa.ForeignKey("poly_markets.market_id")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, server_default=sa.func.now()
    )
    our_p: Mapped[float] = mapped_column(Numeric(5, 4))
    market_p: Mapped[float] = mapped_column(Numeric(5, 4))
    edge: Mapped[float] = mapped_column(Numeric(5, 4))
    direction: Mapped[str] = mapped_column(String(5))
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    forecast_source: Mapped[str] = mapped_column(String(30))
    forecast_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    traded: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)


# ---------------------------------------------------------------------------
# Table 5: Trade
# ---------------------------------------------------------------------------

class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_status_placed", "status", "placed_at"),
        Index("ix_trades_market_status", "market_id", "status"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        _BigIntPK, sa.ForeignKey("opportunities.id"), index=True, nullable=True
    )
    poly_order_id: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    # Denormalized position metadata — Phase 2.1. These fields let the Trade
    # table double as the source-of-truth for open positions so state survives
    # process crashes. Populated from the originating Opportunity at entry.
    market_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(5), nullable=True)     # "YES"/"NO"
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_slug: Mapped[str | None] = mapped_column(String(200), nullable=True)
    peak_pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    token_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_usdc: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    limit_price: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    fill_size_usdc: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    settlement_result: Mapped[str | None] = mapped_column(String(10), nullable=True)
    pnl_usdc: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)


# ---------------------------------------------------------------------------
# Table 6: CityIcaoMapping
# ---------------------------------------------------------------------------

class CityIcaoMapping(Base):
    __tablename__ = "city_icao_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_pattern: Mapped[str] = mapped_column(String(100), index=True)
    station_id: Mapped[str] = mapped_column(
        String(10), sa.ForeignKey("icao_stations.station_id")
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )


# ---------------------------------------------------------------------------
# Table 7: ForecastSnapshot
# ---------------------------------------------------------------------------

class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"
    __table_args__ = (
        Index("ix_forecast_station_created", "station_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(
        String(10), sa.ForeignKey("icao_stations.station_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )
    source: Mapped[str] = mapped_column(String(20))
    model_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    forecast_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Table 8: EdgeCalibration
# ---------------------------------------------------------------------------

class EdgeCalibration(Base):
    __tablename__ = "edge_calibration"
    __table_args__ = (
        Index(
            "ix_calibration_source_resolved", "forecast_source", "resolved_at"
        ),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int | None] = mapped_column(
        _BigIntPK, sa.ForeignKey("opportunities.id"), nullable=True
    )
    our_p: Mapped[float] = mapped_column(Numeric(5, 4))
    actual_outcome: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    forecast_source: Mapped[str] = mapped_column(String(30))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
    station_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    hours_to_resolution: Mapped[float | None] = mapped_column(
        Numeric(6, 1), nullable=True
    )
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edge_at_entry: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    calibrated_p: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)


# ---------------------------------------------------------------------------
# Table 9: RiskConfigEntry
# ---------------------------------------------------------------------------

class RiskConfigEntry(Base):
    __tablename__ = "risk_config"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )


# ---------------------------------------------------------------------------
# Table 9b: SystemState — Phase 2.2/2.3 crash-safe runtime counters
# ---------------------------------------------------------------------------

class SystemState(Base):
    """Generic key/value store for stateful runtime counters.

    Used for:
      - daily_loss:<YYYY-MM-DD> → float str (auto-reset when date rolls over)
      - completed_trades → int str (bootstrap phase counter, survives restarts)
      - is_paused → "true"/"false" (manual kill-switch)
    """
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )


# ---------------------------------------------------------------------------
# Table 10: SystemEvent
# ---------------------------------------------------------------------------

class SystemEvent(Base):
    __tablename__ = "system_events"
    __table_args__ = (
        Index("ix_events_severity_created", "severity", "created_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now()
    )
