"""Tests for polymarket_weather.weather.collector."""

import pytest
from datetime import datetime, timezone

from polymarket_weather.weather.collector import (
    MetarCollector,
    parse_metar_response,
    parse_remarks_temperature,
)

# ---------------------------------------------------------------------------
# Remarks T-group parsing
# ---------------------------------------------------------------------------

def test_parse_remarks_positive_temp():
    raw = "KJFK 050600Z 27010KT 10SM CLR 16/08 A2992 RMK AO2 T01560083"
    temp = parse_remarks_temperature(raw)
    assert temp == 15.6


def test_parse_remarks_negative_temp():
    raw = "KORD 050600Z 36015KT 10SM CLR M02/M08 A3020 RMK AO2 T10221083"
    temp = parse_remarks_temperature(raw)
    assert temp == -2.2


def test_parse_remarks_zero_temp():
    raw = "KDEN 050600Z 00000KT 10SM CLR 00/M05 A3030 RMK AO2 T00001050"
    temp = parse_remarks_temperature(raw)
    assert temp == 0.0


def test_parse_remarks_no_tgroup():
    raw = "KJFK 050600Z 27010KT 10SM CLR 16/08 A2992"
    temp = parse_remarks_temperature(raw)
    assert temp is None


# ---------------------------------------------------------------------------
# METAR response parsing
# ---------------------------------------------------------------------------

SAMPLE_METAR = [
    {
        "icaoId": "KJFK",
        "obsTime": 1712300400,
        "temp": 15.6,
        "dewp": 8.3,
        "wdir": 270,
        "wspd": 10,
        "wgst": None,
        "altim": 1013.2,
        "slp": 1012.8,
        "visib": "10+",
        "clouds": [{"cover": "FEW", "base": 250}],
        "wxString": None,
        "metarType": "METAR",
        "rawOb": "KJFK 050600Z 27010KT 10SM FEW250 16/08 A2992 RMK AO2 T01560083",
    }
]


def test_parse_metar_response_basic():
    readings = parse_metar_response(SAMPLE_METAR)
    assert len(readings) == 1
    r = readings[0]
    assert r["station_id"] == "KJFK"
    assert r["temp"] == 15.6
    assert r["dewp"] == 8.3
    assert r["wdir"] == 270
    assert r["wspd"] == 10
    assert r["visib"] == "10+"
    assert r["cloud_cover"] == [{"cover": "FEW", "base": 250}]
    assert r["metar_type"] == "METAR"


def test_parse_metar_precise_temp():
    readings = parse_metar_response(SAMPLE_METAR)
    assert readings[0]["temp_precise_c"] == 15.6


def test_parse_metar_observed_at():
    readings = parse_metar_response(SAMPLE_METAR)
    r = readings[0]
    assert isinstance(r["observed_at"], datetime)
    assert r["observed_at"].tzinfo == timezone.utc


def test_parse_metar_missing_station():
    data = [{"obsTime": 123}]  # No icaoId
    readings = parse_metar_response(data)
    assert len(readings) == 0


def test_parse_metar_empty():
    readings = parse_metar_response([])
    assert len(readings) == 0


def test_parse_metar_multiple_stations():
    data = [
        {"icaoId": "KJFK", "obsTime": 100, "temp": 15.0, "rawOb": ""},
        {"icaoId": "KLAX", "obsTime": 100, "temp": 20.0, "rawOb": ""},
    ]
    readings = parse_metar_response(data)
    assert len(readings) == 2
    assert readings[0]["station_id"] == "KJFK"
    assert readings[1]["station_id"] == "KLAX"


def test_parse_metar_slp_mapped():
    readings = parse_metar_response(SAMPLE_METAR)
    assert readings[0]["slp"] == 1012.8


def test_parse_metar_null_visib():
    data = [{"icaoId": "KJFK", "obsTime": 100, "visib": None, "rawOb": ""}]
    readings = parse_metar_response(data)
    assert readings[0]["visib"] is None


# ---------------------------------------------------------------------------
# MetarCollector with mock DB
# ---------------------------------------------------------------------------

@pytest.fixture
async def collector_with_db():
    """Create a collector with in-memory SQLite DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from polymarket_weather.db.models import Base, IcaoStation

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed a station
    async with factory() as session:
        station = IcaoStation(
            station_id="KJFK",
            city_name="New York",
            country_code="US",
            lat=40.64,
            lon=-73.78,
            is_active=True,
        )
        session.add(station)
        await session.commit()

    collector = MetarCollector(
        api_url="https://aviationweather.gov/api/data/metar",
        user_agent="Test/1.0",
        hours_lookback=3,
        max_results=400,
        session_factory=factory,
    )

    yield collector, factory
    await engine.dispose()


async def test_fetch_and_store_dedup(collector_with_db):
    """Verify deduplication: storing same readings twice should not create duplicates."""
    collector, factory = collector_with_db
    from sqlalchemy import select

    from polymarket_weather.db.models import MetarReading

    readings = parse_metar_response(SAMPLE_METAR)

    # Store manually first time
    async with factory() as session:
        for r in readings:
            session.add(
                MetarReading(
                    station_id=r["station_id"],
                    observed_at=r["observed_at"],
                    fetched_at=datetime.now(timezone.utc),
                    temp=r["temp"],
                    raw_metar=r["raw_metar"],
                )
            )
        await session.commit()

    # fetch_and_store with empty list should return 0 (no fetch, no store)
    count = await collector.fetch_and_store([])
    assert count == 0

    # Confirm only one record in DB
    async with factory() as session:
        result = await session.execute(select(MetarReading))
        rows = result.scalars().all()
    assert len(rows) == 1


async def test_collector_fetch_no_http(collector_with_db):
    """fetch_metar returns empty list when http client not started."""
    collector, _ = collector_with_db
    # _http is None (start() never called)
    result = await collector.fetch_metar(["KJFK"])
    assert result == []


async def test_collector_fetch_empty_stations(collector_with_db):
    """fetch_metar returns empty list for empty station list."""
    collector, _ = collector_with_db
    await collector.start()
    result = await collector.fetch_metar([])
    assert result == []
    await collector.stop()


async def test_check_staleness_no_session():
    """check_staleness returns all stations when no session_factory."""
    collector = MetarCollector(
        api_url="https://example.com",
        user_agent="Test/1.0",
        hours_lookback=3,
        max_results=400,
        session_factory=None,
    )
    stale = await collector.check_staleness(["KJFK", "KLAX"], stale_threshold_seconds=3600)
    assert stale == []


async def test_check_staleness_with_db(collector_with_db):
    """check_staleness reports station with no last_report_at as stale."""
    collector, _ = collector_with_db
    stale = await collector.check_staleness(["KJFK"], stale_threshold_seconds=60)
    assert "KJFK" in stale
