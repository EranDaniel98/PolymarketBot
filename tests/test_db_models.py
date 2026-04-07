"""Tests for SQLAlchemy ORM models using an in-memory SQLite/aiosqlite database."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from polymarket_weather.db.models import (
    Base,
    IcaoStation,
    MetarReading,
    PolyMarket,
    Opportunity,
    Trade,
    CityIcaoMapping,
    ForecastSnapshot,
    EdgeCalibration,
    RiskConfigEntry,
    SystemEvent,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# IcaoStation
# ---------------------------------------------------------------------------

async def test_create_station(db_session):
    station = IcaoStation(
        station_id="KJFK",
        city_name="New York",
        country_code="US",
        lat=40.6413,
        lon=-73.7781,
        is_active=True,
    )
    db_session.add(station)
    await db_session.commit()

    result = await db_session.execute(
        select(IcaoStation).where(IcaoStation.station_id == "KJFK")
    )
    row = result.scalar_one()
    assert row.city_name == "New York"
    assert row.country_code == "US"
    assert row.is_active is True


# ---------------------------------------------------------------------------
# MetarReading
# ---------------------------------------------------------------------------

async def test_create_metar_reading(db_session):
    station = IcaoStation(
        station_id="KJFK",
        city_name="New York",
        country_code="US",
        lat=40.6413,
        lon=-73.7781,
        is_active=True,
    )
    db_session.add(station)
    await db_session.flush()

    reading = MetarReading(
        station_id="KJFK",
        observed_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        temp=15.6,
        dewp=8.3,
        wspd=10,
        wdir=270,
        visib="10+",
        cloud_cover=[{"cover": "FEW", "base": 250}],
        metar_type="METAR",
        temp_precise_c=15.6,
        raw_metar="KJFK 051200Z 27010KT 10SM FEW250 16/08 A2992",
    )
    db_session.add(reading)
    await db_session.commit()

    result = await db_session.execute(
        select(MetarReading).where(MetarReading.station_id == "KJFK")
    )
    row = result.scalar_one()
    assert float(row.temp) == 15.6
    assert row.wdir == 270
    assert row.visib == "10+"
    assert row.metar_type == "METAR"


# ---------------------------------------------------------------------------
# PolyMarket (range market)
# ---------------------------------------------------------------------------

async def test_create_poly_market_with_range(db_session):
    market = PolyMarket(
        market_id="0xabc",
        question="Will NYC high be 50-54F?",
        city_name="new york",
        metric="temperature",
        threshold=50.0,
        threshold_upper=54.0,
        unit="F",
        direction="range",
        event_id="evt_123",
        yes_token_id="tok_y",
        no_token_id="tok_n",
        status="active",
    )
    db_session.add(market)
    await db_session.commit()

    result = await db_session.execute(
        select(PolyMarket).where(PolyMarket.market_id == "0xabc")
    )
    row = result.scalar_one()
    assert row.event_id == "evt_123"
    assert float(row.threshold_upper) == 54.0
    assert float(row.threshold) == 50.0
    assert row.direction == "range"


# ---------------------------------------------------------------------------
# Opportunity + Trade (FK chain)
# ---------------------------------------------------------------------------

async def test_opportunity_and_trade(db_session):
    market = PolyMarket(
        market_id="0xabc",
        question="Test",
        city_name="nyc",
        metric="temperature",
        threshold=50.0,
        unit="F",
        direction="above",
        yes_token_id="t1",
        no_token_id="t2",
        status="active",
    )
    db_session.add(market)
    await db_session.flush()

    opp = Opportunity(
        market_id="0xabc",
        our_p=0.75,
        market_p=0.55,
        edge=0.20,
        direction="YES",
        confidence=0.85,
        forecast_source="metar",
        traded=True,
    )
    db_session.add(opp)
    await db_session.flush()

    trade = Trade(
        opportunity_id=opp.id,
        token_id="t1",
        size_usdc=25.0,
        limit_price=0.55,
        status="filled",
    )
    db_session.add(trade)
    await db_session.commit()

    assert trade.opportunity_id == opp.id
    assert trade.status == "filled"

    result = await db_session.execute(
        select(Opportunity).where(Opportunity.id == opp.id)
    )
    fetched_opp = result.scalar_one()
    assert fetched_opp.traded is True
    assert float(fetched_opp.edge) == pytest.approx(0.20, abs=1e-4)


# ---------------------------------------------------------------------------
# CityIcaoMapping
# ---------------------------------------------------------------------------

async def test_city_icao_mapping(db_session):
    station = IcaoStation(
        station_id="KLAX",
        city_name="Los Angeles",
        country_code="US",
        lat=33.9425,
        lon=-118.4081,
        is_active=True,
    )
    db_session.add(station)
    await db_session.flush()

    mapping = CityIcaoMapping(
        city_pattern="los angeles",
        station_id="KLAX",
        priority=10,
    )
    db_session.add(mapping)
    await db_session.commit()

    result = await db_session.execute(
        select(CityIcaoMapping).where(CityIcaoMapping.city_pattern == "los angeles")
    )
    row = result.scalar_one()
    assert row.station_id == "KLAX"
    assert row.priority == 10


# ---------------------------------------------------------------------------
# ForecastSnapshot
# ---------------------------------------------------------------------------

async def test_forecast_snapshot(db_session):
    station = IcaoStation(
        station_id="KORD",
        city_name="Chicago",
        country_code="US",
        lat=41.9742,
        lon=-87.9073,
        is_active=True,
    )
    db_session.add(station)
    await db_session.flush()

    snap = ForecastSnapshot(
        station_id="KORD",
        source="nwp",
        model_name="GFS",
        forecast_data={"temp_high": 55.2, "precip_prob": 0.1},
    )
    db_session.add(snap)
    await db_session.commit()

    result = await db_session.execute(
        select(ForecastSnapshot).where(ForecastSnapshot.station_id == "KORD")
    )
    row = result.scalar_one()
    assert row.source == "nwp"
    assert row.model_name == "GFS"
    assert row.forecast_data["temp_high"] == 55.2


# ---------------------------------------------------------------------------
# EdgeCalibration
# ---------------------------------------------------------------------------

async def test_edge_calibration(db_session):
    cal = EdgeCalibration(
        our_p=0.72,
        actual_outcome=True,
        forecast_source="metar",
        station_id="KJFK",
        hours_to_resolution=24.0,
        month=4,
        edge_at_entry=0.15,
        calibrated_p=0.70,
    )
    db_session.add(cal)
    await db_session.commit()

    result = await db_session.execute(select(EdgeCalibration))
    row = result.scalar_one()
    assert row.actual_outcome is True
    assert float(row.calibrated_p) == pytest.approx(0.70, abs=1e-4)
    assert row.month == 4


# ---------------------------------------------------------------------------
# RiskConfigEntry
# ---------------------------------------------------------------------------

async def test_risk_config_entry(db_session):
    entry = RiskConfigEntry(key="max_trade_usdc", value="50.00")
    db_session.add(entry)
    await db_session.commit()

    result = await db_session.execute(
        select(RiskConfigEntry).where(RiskConfigEntry.key == "max_trade_usdc")
    )
    row = result.scalar_one()
    assert row.value == "50.00"

    # Update
    row.value = "100.00"
    await db_session.commit()

    result2 = await db_session.execute(
        select(RiskConfigEntry).where(RiskConfigEntry.key == "max_trade_usdc")
    )
    updated = result2.scalar_one()
    assert updated.value == "100.00"


# ---------------------------------------------------------------------------
# SystemEvent
# ---------------------------------------------------------------------------

async def test_system_event(db_session):
    event = SystemEvent(
        event_type="metar_poll",
        severity="info",
        message="Fetched 20 stations",
        details={"count": 20},
    )
    db_session.add(event)
    await db_session.commit()

    result = await db_session.execute(select(SystemEvent))
    row = result.scalar_one()
    assert row.event_type == "metar_poll"
    assert row.severity == "info"
    assert row.details == {"count": 20}


# ---------------------------------------------------------------------------
# session.py — init_db / get_session_factory / dispose_db
# ---------------------------------------------------------------------------

async def test_session_module():
    from polymarket_weather.db import session as sess_mod

    # Reset any lingering state
    await sess_mod.dispose_db()

    with pytest.raises(RuntimeError, match="not initialized"):
        sess_mod.get_session_factory()

    with pytest.raises(RuntimeError, match="not initialized"):
        sess_mod.get_engine()

    sess_mod.init_db("sqlite+aiosqlite:///:memory:")
    factory = sess_mod.get_session_factory()
    engine = sess_mod.get_engine()
    assert factory is not None
    assert engine is not None

    await sess_mod.dispose_db()

    with pytest.raises(RuntimeError):
        sess_mod.get_engine()
