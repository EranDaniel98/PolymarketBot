# Weather Arbitrage Rewrite — Plan 1: Data Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the data collection and forecast pipeline — PostgreSQL schema, METAR/NWP weather collection, Polymarket market scanning/parsing, and the probability forecast engine. When complete, the system collects live weather data, discovers weather markets, and computes P(condition met) with confidence intervals — running in observation-only mode.

**Architecture:** New `polymarket_weather/` package replaces `polymarket_bot/`. PostgreSQL via SQLAlchemy async + asyncpg. METAR from aviationweather.gov, NWP ensembles from Open-Meteo `/v1/ensemble`. Market discovery via Gamma API `/events`. Forecast engine converts temperature distributions to probabilities via t-distribution CDF. All config from YAML, zero hardcoded values.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async) + asyncpg, Alembic, httpx, scipy, pydantic, pyyaml

**Spec:** `docs/superpowers/specs/2026-04-05-weather-arbitrage-rewrite.md` (v2 with review corrections)

---

## File Map

```
polymarket_weather/
├── __init__.py
├── __main__.py
├── config.py                 # Config dataclasses + YAML loading
├── event_bus.py              # Pub/sub (carried from existing)
├── db/
│   ├── __init__.py
│   ├── models.py             # SQLAlchemy ORM models (10 tables)
│   └── session.py            # Engine + session factory
├── weather/
│   ├── __init__.py
│   ├── collector.py          # METAR + TAF polling
│   ├── nwp.py                # Open-Meteo ensemble fetching
│   ├── city_mapper.py        # City -> ICAO resolution
│   └── forecast.py           # P(condition met) calculator
├── markets/
│   ├── __init__.py
│   ├── scanner.py            # Polymarket market discovery
│   └── parser.py             # Question -> ParsedMarket
config/
├── cities.json               # City -> ICAO seed data
config.yaml                   # Main config (new structure)
config.example.yaml           # Template with empty secrets
.env.example                  # Secret variable names
migrations/                   # Alembic
├── env.py
├── alembic.ini
├── versions/
tests/
├── conftest.py
├── test_config.py
├── test_db_models.py
├── test_collector.py
├── test_nwp.py
├── test_city_mapper.py
├── test_forecast.py
├── test_scanner.py
├── test_parser.py
pyproject.toml                # Updated deps
docker-compose.yml
```

---

## Task 1: Project Scaffold + Dependencies

**Files:**
- Create: `polymarket_weather/__init__.py`
- Create: `polymarket_weather/__main__.py`
- Modify: `pyproject.toml`
- Create: `.env.example`
- Create: `config.example.yaml`

- [ ] **Step 1: Create the new package directory**

```bash
mkdir -p polymarket_weather/db polymarket_weather/weather polymarket_weather/markets polymarket_weather/trading polymarket_weather/alerts polymarket_weather/api
```

Create `polymarket_weather/__init__.py`:
```python
__version__ = "2.0.0"
```

Create empty `__init__.py` in each subpackage.

- [ ] **Step 2: Update pyproject.toml**

Replace the existing pyproject.toml with new dependencies:

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "polymarket-weather"
version = "2.0.0"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    "httpx>=0.27.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "scipy>=1.14.0",
    "numpy>=2.0.0",
    "rich>=13.9.0",
    "py-clob-client>=0.17.0",
    "python-telegram-bot>=21.0",
    "apscheduler>=4.0.0",
    "structlog>=24.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24.0", "pytest-mock>=3.14.0", "ruff>=0.6.0"]
web = ["fastapi>=0.115.0", "uvicorn>=0.32.0"]

[project.scripts]
polymarket-weather = "polymarket_weather.__main__:main"
```

- [ ] **Step 3: Create .env.example and config.example.yaml**

`.env.example`:
```
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_PRIVATE_KEY=
DATABASE_URL=postgresql+asyncpg://polymarket:polymarket@localhost:5432/polymarket_weather
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DASH_PASS=
```

`config.example.yaml` — full config structure from spec Section 4 with empty secret fields and documented defaults. (Copy the config YAML block from the spec verbatim.)

- [ ] **Step 4: Create __main__.py stub**

```python
import asyncio
import sys

def main():
    from polymarket_weather.app import run_bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Commit scaffold**

```bash
git add polymarket_weather/ pyproject.toml .env.example config.example.yaml
git commit -m "feat: scaffold polymarket_weather package with new deps"
```

---

## Task 2: Config System

**Files:**
- Create: `polymarket_weather/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for config loading**

`tests/test_config.py`:
```python
import pytest
from pathlib import Path

def test_load_config_from_yaml(tmp_path):
    yaml_content = """
database:
  url: "postgresql+asyncpg://user:pass@localhost/test"
weather:
  metar:
    poll_interval: 1800
    stale_threshold: 10800
    api_url: "https://aviationweather.gov/api/data/metar"
    hours_lookback: 3
  nwp:
    poll_interval: 21600
    api_url: "https://api.open-meteo.com/v1/ensemble"
    models: ["ecmwf_ifs025"]
forecast:
  metar_only_hours: 6
  blend_cutoff_hours: 30
  metar_blend_weight: 0.6
  min_confidence: 0.70
  distribution_df: 7
edge:
  min_edge_metar: 0.06
  min_edge_nwp: 0.12
  kelly_fraction: 0.5
risk:
  max_position_usdc: 50
  max_total_exposure_usdc: 600
  max_open_positions: 20
trading:
  paper_trading: true
  paper_balance: 1000
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)

    from polymarket_weather.config import load_config
    config = load_config(config_file)

    assert config.database.url == "postgresql+asyncpg://user:pass@localhost/test"
    assert config.weather.metar.poll_interval == 1800
    assert config.forecast.distribution_df == 7
    assert config.edge.min_edge_metar == 0.06
    assert config.risk.max_total_exposure_usdc == 600
    assert config.trading.paper_trading is True


def test_env_override(tmp_path, monkeypatch):
    yaml_content = """
database:
  url: "placeholder"
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_content)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://real:real@prod/db")

    from polymarket_weather.config import load_config
    config = load_config(config_file)
    assert config.database.url == "postgresql+asyncpg://real:real@prod/db"


def test_missing_config_file():
    from polymarket_weather.config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd polymarket_weather && python -m pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_weather.config'`

- [ ] **Step 3: Implement config.py**

`polymarket_weather/config.py` — dataclass hierarchy matching the spec's full YAML structure. Key sections: `DatabaseConfig`, `MetarConfig`, `NwpConfig`, `WeatherConfig`, `ForecastConfig`, `EdgeConfig`, `RiskConfig`, `TradingConfig`, `SchedulerConfig`, `NotificationsConfig`, `CalibrationConfig`, `WebConfig`, `FeeConfig`, `LoggingConfig`, `BotConfig`.

Use the same `_dict_to_dataclass` + `_apply_env_overrides` pattern from the existing `config.py`, expanded for the new structure. All fields have defaults matching the spec.

Env map:
```python
_ENV_MAP = {
    "DATABASE_URL": ("database", "url"),
    "POLYMARKET_API_KEY": ("polymarket", "api_key"),
    "POLYMARKET_API_SECRET": ("polymarket", "api_secret"),
    "POLYMARKET_PRIVATE_KEY": ("polymarket", "private_key"),
    "TELEGRAM_BOT_TOKEN": ("notifications.telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("notifications.telegram", "chat_id"),
    "DASH_PASS": ("web", "dash_pass"),
}
```

Call `load_dotenv()` at the top of `load_config()`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_weather/config.py tests/test_config.py
git commit -m "feat: config system with YAML + env override for weather bot"
```

---

## Task 3: Event Bus (carry forward)

**Files:**
- Create: `polymarket_weather/event_bus.py`

- [ ] **Step 1: Copy event_bus.py from existing bot**

Copy `polymarket_bot/event_bus.py` to `polymarket_weather/event_bus.py` unchanged. It's 30 lines of clean pub/sub with no dependencies on the old architecture.

- [ ] **Step 2: Commit**

```bash
git add polymarket_weather/event_bus.py
git commit -m "feat: carry forward event bus from existing bot"
```

---

## Task 4: Database Models + Session

**Files:**
- Create: `polymarket_weather/db/models.py`
- Create: `polymarket_weather/db/session.py`
- Create: `tests/test_db_models.py`

- [ ] **Step 1: Write failing test for DB models**

`tests/test_db_models.py`:
```python
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy import select

@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory SQLite session for testing (asyncpg not needed for model tests)."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from polymarket_weather.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_icao_station(db_session):
    from polymarket_weather.db.models import IcaoStation
    station = IcaoStation(
        station_id="KJFK", city_name="New York", country_code="US",
        lat=40.6413, lon=-73.7781, elevation_m=4, is_active=True,
    )
    db_session.add(station)
    await db_session.commit()

    result = await db_session.execute(select(IcaoStation).where(IcaoStation.station_id == "KJFK"))
    row = result.scalar_one()
    assert row.city_name == "New York"
    assert row.is_active is True


@pytest.mark.asyncio
async def test_create_metar_reading(db_session):
    from polymarket_weather.db.models import IcaoStation, MetarReading
    station = IcaoStation(station_id="KJFK", city_name="New York", country_code="US",
                          lat=40.6413, lon=-73.7781, is_active=True)
    db_session.add(station)
    await db_session.flush()

    reading = MetarReading(
        station_id="KJFK",
        observed_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        temp=15.6, dewp=8.3, altim=1013.2, wspd=10, wdir=270,
        visib="10+", raw_metar="KJFK 051200Z 27010KT 10SM CLR 16/08 A2992",
    )
    db_session.add(reading)
    await db_session.commit()

    result = await db_session.execute(select(MetarReading).where(MetarReading.station_id == "KJFK"))
    row = result.scalar_one()
    assert row.temp == 15.6
    assert row.wdir == 270


@pytest.mark.asyncio
async def test_create_poly_market(db_session):
    from polymarket_weather.db.models import PolyMarket
    market = PolyMarket(
        market_id="0xabc123", question="Will NYC high temp be 50-54F on April 15?",
        city_name="new york", metric="temperature", threshold=50.0, threshold_upper=54.0,
        unit="F", direction="range", event_id="evt_123",
        yes_token_id="tok_yes", no_token_id="tok_no", status="active",
    )
    db_session.add(market)
    await db_session.commit()

    result = await db_session.execute(select(PolyMarket).where(PolyMarket.market_id == "0xabc123"))
    row = result.scalar_one()
    assert row.city_name == "new york"
    assert row.event_id == "evt_123"
    assert row.threshold_upper == 54.0


@pytest.mark.asyncio
async def test_create_opportunity_and_trade(db_session):
    from polymarket_weather.db.models import PolyMarket, Opportunity, Trade

    market = PolyMarket(market_id="0xabc", question="Test", city_name="nyc",
                        metric="temperature", threshold=50.0, unit="F", direction="above",
                        yes_token_id="t1", no_token_id="t2", status="active")
    db_session.add(market)
    await db_session.flush()

    opp = Opportunity(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        traded=True,
    )
    db_session.add(opp)
    await db_session.flush()

    trade = Trade(
        opportunity_id=opp.id, token_id="t1", size_usdc=25.0,
        limit_price=0.55, status="filled",
    )
    db_session.add(trade)
    await db_session.commit()

    assert trade.opportunity_id == opp.id
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_db_models.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Implement db/models.py**

SQLAlchemy 2.0 declarative models for all 10 tables from spec Section 5, plus the v2 corrections (Section 15):
- `IcaoStation` (PK: station_id)
- `MetarReading` (PK: id BIGSERIAL, UNIQUE: station_id + observed_at) — includes v2 fields: wdir, wgst, cloud_cover (JSONB), wx_string, slp_hpa, metar_type, temp_precise_c
- `PolyMarket` (PK: market_id) — includes v2 fields: event_id, group_id, threshold_upper (for range markets)
- `Opportunity` (PK: id BIGSERIAL, FK: market_id) — includes forecast_snapshot JSONB
- `Trade` (PK: id BIGSERIAL, FK: opportunity_id)
- `CityIcaoMapping` (PK: id SERIAL)
- `ForecastSnapshot` (PK: id BIGSERIAL) — JSONB forecast_data
- `EdgeCalibration` (PK: id BIGSERIAL) — includes v2 fields: station_id, hours_to_resolution, month, edge_at_entry, calibrated_p
- `RiskConfig` (PK: key)
- `SystemEvent` (PK: id BIGSERIAL) — JSONB details

Use `DateTime(timezone=True)` for all timestamps. Use `sqlalchemy.dialects.postgresql.JSONB` for JSON columns. Include all indexes from spec Section 15.10.

Base class:
```python
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

class Base(AsyncAttrs, DeclarativeBase):
    pass
```

- [ ] **Step 4: Implement db/session.py**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

_engine = None
_session_factory = None

def init_db(database_url: str):
    global _engine, _session_factory
    _engine = create_async_engine(
        database_url,
        pool_size=10, max_overflow=5,
        pool_pre_ping=True, pool_recycle=1800,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

def get_session_factory():
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory

def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine

async def dispose_db():
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
    _engine = None
    _session_factory = None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_db_models.py -v
```
Expected: 4 PASS (using SQLite for testing, asyncpg for production)

- [ ] **Step 6: Commit**

```bash
git add polymarket_weather/db/ tests/test_db_models.py
git commit -m "feat: SQLAlchemy ORM models for all 10 tables + session factory"
```

---

## Task 5: Alembic Migrations Setup

**Files:**
- Create: `migrations/env.py`
- Create: `migrations/alembic.ini`
- Create: `migrations/versions/` (empty)

- [ ] **Step 1: Initialize Alembic with async template**

```bash
cd polymarket_weather && alembic init --template async migrations
```

- [ ] **Step 2: Configure env.py**

Edit `migrations/env.py` to import `Base.metadata` from `polymarket_weather.db.models` and read `DATABASE_URL` from environment. Use `NullPool` for migrations.

Edit `alembic.ini` to set `sqlalchemy.url` to empty (will be overridden by env.py).

- [ ] **Step 3: Generate initial migration**

```bash
alembic revision --autogenerate -m "initial schema - 10 tables"
```

- [ ] **Step 4: Commit**

```bash
git add migrations/
git commit -m "feat: Alembic migration setup with initial schema"
```

---

## Task 6: City Mapper + cities.json

**Files:**
- Create: `config/cities.json`
- Create: `polymarket_weather/weather/city_mapper.py`
- Create: `tests/test_city_mapper.py`

- [ ] **Step 1: Create cities.json seed data**

`config/cities.json` — seed with ~20 major cities that appear on Polymarket weather markets. Each entry has `city_aliases`, `stations`, `primary_station`, `region`, `country`, `lat`, `lon`. Include: NYC, LA, Chicago, Miami, Phoenix, Houston, Dallas, Denver, Seattle, SF, Boston, Atlanta, DC, Philly, Detroit, Minneapolis, London, Tokyo, Seoul, Mexico City.

- [ ] **Step 2: Write failing tests**

`tests/test_city_mapper.py`:
```python
import pytest
from polymarket_weather.weather.city_mapper import CityMapper

@pytest.fixture
def mapper(tmp_path):
    cities = [
        {"city_aliases": ["new york", "nyc", "new york city"],
         "stations": ["KJFK", "KLGA", "KEWR"],
         "primary_station": "KJFK", "region": "northeast_us",
         "country": "US", "lat": 40.64, "lon": -73.78},
        {"city_aliases": ["tokyo"],
         "stations": ["RJTT", "RJAA"],
         "primary_station": "RJTT", "region": "kanto_jp",
         "country": "JP", "lat": 35.55, "lon": 139.78},
    ]
    import json
    cities_file = tmp_path / "cities.json"
    cities_file.write_text(json.dumps(cities))
    return CityMapper(cities_file)

def test_resolve_city_exact_match(mapper):
    result = mapper.resolve("new york")
    assert result is not None
    assert result.primary_station == "KJFK"
    assert result.region == "northeast_us"

def test_resolve_city_alias(mapper):
    result = mapper.resolve("nyc")
    assert result is not None
    assert result.primary_station == "KJFK"

def test_resolve_unknown_city(mapper):
    result = mapper.resolve("atlantis")
    assert result is None

def test_all_stations(mapper):
    result = mapper.resolve("new york")
    assert set(result.all_stations) == {"KJFK", "KLGA", "KEWR"}

def test_all_cities(mapper):
    cities = mapper.all_city_names()
    assert "new york" in cities
    assert "tokyo" in cities
```

- [ ] **Step 3: Implement city_mapper.py**

```python
from dataclasses import dataclass
from pathlib import Path
import json

@dataclass
class CityMatch:
    city_name: str
    primary_station: str
    all_stations: list[str]
    region: str
    country: str
    lat: float
    lon: float

class CityMapper:
    def __init__(self, cities_file: Path):
        with open(cities_file) as f:
            self._cities = json.load(f)
        self._alias_map: dict[str, dict] = {}
        for city in self._cities:
            for alias in city["city_aliases"]:
                self._alias_map[alias.lower()] = city

    def resolve(self, city_name: str) -> CityMatch | None:
        city = self._alias_map.get(city_name.lower())
        if not city:
            return None
        return CityMatch(
            city_name=city["city_aliases"][0],
            primary_station=city["primary_station"],
            all_stations=city["stations"],
            region=city["region"],
            country=city["country"],
            lat=city["lat"], lon=city["lon"],
        )

    def all_city_names(self) -> list[str]:
        return [c["city_aliases"][0] for c in self._cities]
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_city_mapper.py -v
```
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add config/cities.json polymarket_weather/weather/city_mapper.py tests/test_city_mapper.py
git commit -m "feat: city mapper with ICAO station resolution from cities.json"
```

---

## Task 7: METAR Collector

**Files:**
- Create: `polymarket_weather/weather/collector.py`
- Create: `tests/test_collector.py`

- [ ] **Step 1: Write failing tests**

Test the METAR response parsing (not the HTTP call — mock that):

```python
import pytest
from polymarket_weather.weather.collector import MetarCollector, parse_metar_response

SAMPLE_METAR_JSON = [
    {
        "icaoId": "KJFK", "obsTime": 1712300400,
        "temp": 15.6, "dewp": 8.3, "wdir": 270, "wspd": 10, "wgst": None,
        "altim": 1013.2, "slp": 1012.8, "visib": "10+",
        "clouds": [{"cover": "FEW", "base": 250}],
        "wxString": None, "metarType": "METAR",
        "rawOb": "KJFK 050600Z 27010KT 10SM FEW250 16/08 A2992 RMK AO2 T01560083",
    }
]

def test_parse_metar_response():
    readings = parse_metar_response(SAMPLE_METAR_JSON)
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

def test_parse_remarks_tgroup():
    from polymarket_weather.weather.collector import parse_remarks_temperature
    raw = "KJFK 050600Z 27010KT 10SM CLR 16/08 A2992 RMK AO2 T01560083"
    temp = parse_remarks_temperature(raw)
    assert temp == 15.6  # T0156 = +15.6C

def test_parse_remarks_negative_temp():
    from polymarket_weather.weather.collector import parse_remarks_temperature
    raw = "KORD 050600Z 36015KT 10SM CLR M02/M08 A3020 RMK AO2 T10221083"
    temp = parse_remarks_temperature(raw)
    assert temp == -2.2  # T1022 = -2.2C (1 prefix = negative)

def test_dedup_readings():
    from polymarket_weather.weather.collector import should_store_reading
    # Same station + same obsTime should be skipped
    existing = {("KJFK", 1712300400)}
    assert should_store_reading("KJFK", 1712300400, existing) is False
    assert should_store_reading("KJFK", 1712304000, existing) is True
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement collector.py**

Key functions:
- `parse_metar_response(json_data: list[dict]) -> list[dict]` — extracts fields using real API names (`temp`, `dewp`, `wspd`, `wdir`, etc.)
- `parse_remarks_temperature(raw_metar: str) -> float | None` — parses `Txxxxxxxx` group from remarks for 0.1C precision
- `should_store_reading(station_id, obs_time, existing_set) -> bool` — dedup check

Class `MetarCollector`:
- `__init__(config: MetarConfig, session_factory)` — stores config (api_url, poll_interval, stale_threshold, hours_lookback, user_agent)
- `async fetch_and_store(station_ids: list[str]) -> int` — bulk fetch from aviationweather.gov, parse, deduplicate, upsert into `metar_readings`, update `icao_stations.last_report_at`, return count of new readings
- `async check_staleness(stale_threshold: int) -> list[str]` — returns station_ids where last_report_at > threshold

- [ ] **Step 4: Run tests**

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_weather/weather/collector.py tests/test_collector.py
git commit -m "feat: METAR collector with aviationweather.gov parsing + dedup"
```

---

## Task 8: NWP Ensemble Fetcher

**Files:**
- Create: `polymarket_weather/weather/nwp.py`
- Create: `tests/test_nwp.py`

- [ ] **Step 1: Write failing tests**

Test Open-Meteo response parsing:

```python
import pytest
from polymarket_weather.weather.nwp import parse_ensemble_response, NwpFetcher

SAMPLE_ENSEMBLE_JSON = {
    "latitude": 40.64, "longitude": -73.78,
    "hourly": {
        "time": ["2026-04-06T00:00", "2026-04-06T01:00", "2026-04-06T02:00"],
        "temperature_2m_member01": [12.0, 11.5, 11.0],
        "temperature_2m_member02": [13.0, 12.5, 12.0],
        "temperature_2m_member03": [11.5, 11.0, 10.5],
    }
}

def test_parse_ensemble_response():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE_JSON)
    # Should extract member forecasts per hour
    assert len(result.times) == 3
    assert len(result.members) == 3  # 3 members
    assert result.members[0] == [12.0, 11.5, 11.0]  # member01
    assert result.mean_at(0) == pytest.approx(12.167, abs=0.01)  # mean of 12, 13, 11.5
    assert result.std_at(0) > 0  # non-zero spread

def test_ensemble_temperature_at_time():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE_JSON)
    from datetime import datetime, timezone
    target = datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc)
    mean, std = result.at_time(target)
    assert mean == pytest.approx(11.667, abs=0.01)  # mean of 11.5, 12.5, 11.0
```

- [ ] **Step 2: Implement nwp.py**

`EnsembleResult` dataclass holding parsed ensemble data with helper methods (`mean_at`, `std_at`, `at_time`).

`parse_ensemble_response(json_data) -> EnsembleResult` — extracts all `temperature_2m_memberNN` fields.

Class `NwpFetcher`:
- `__init__(config: NwpConfig)` — stores api_url, models, rate limits
- `async fetch_ensemble(lat: float, lon: float, forecast_days: int = 7) -> EnsembleResult` — queries Open-Meteo `/v1/ensemble` with `hourly=temperature_2m&models=ecmwf_ifs025`
- `async fetch_deterministic(lat: float, lon: float, models: list[str]) -> dict[str, list[float]]` — fallback: queries `/v1/forecast` with multiple models

- [ ] **Step 3: Run tests, commit**

---

## Task 9: Market Scanner (adapted)

**Files:**
- Create: `polymarket_weather/markets/scanner.py`
- Create: `tests/test_scanner.py`

- [ ] **Step 1: Write failing tests**

Test Gamma API response parsing:

```python
import pytest
from polymarket_weather.markets.scanner import WeatherMarketScanner, parse_gamma_event

SAMPLE_EVENT = {
    "id": "evt_123",
    "slug": "highest-temperature-in-nyc-on-april-15-2026",
    "title": "Highest temperature in NYC on April 15?",
    "markets": [
        {
            "id": "123",
            "conditionId": "0xabc",
            "question": "Will the high temp in NYC be between 50 and 54 degrees Fahrenheit on April 15?",
            "clobTokenIds": '["tok_yes_1","tok_no_1"]',
            "outcomePrices": '["0.35","0.65"]',
            "active": True, "closed": False,
            "endDateIso": "2026-04-16T00:00:00Z",
            "resolutionSource": "Weather Underground",
        },
        {
            "id": "124",
            "conditionId": "0xdef",
            "question": "Will the high temp in NYC be between 55 and 59 degrees Fahrenheit on April 15?",
            "clobTokenIds": '["tok_yes_2","tok_no_2"]',
            "outcomePrices": '["0.45","0.55"]',
            "active": True, "closed": False,
            "endDateIso": "2026-04-16T00:00:00Z",
        },
    ],
    "tags": [{"label": "weather", "id": 42}],
}

def test_parse_gamma_event():
    markets = parse_gamma_event(SAMPLE_EVENT)
    assert len(markets) == 2
    m = markets[0]
    assert m.market_id == "0xabc"
    assert m.event_id == "evt_123"
    assert m.yes_token_id == "tok_yes_1"
    assert m.no_token_id == "tok_no_1"
    assert m.current_price == 0.35
    assert m.resolution_source == "Weather Underground"

def test_parse_clob_token_ids_json_string():
    from polymarket_weather.markets.scanner import parse_clob_tokens
    tokens = parse_clob_tokens('["abc","def"]')
    assert tokens == ("abc", "def")

def test_parse_clob_token_ids_list():
    from polymarket_weather.markets.scanner import parse_clob_tokens
    tokens = parse_clob_tokens(["abc", "def"])
    assert tokens == ("abc", "def")
```

- [ ] **Step 2: Implement scanner.py**

Adapted from existing `polymarket_bot/scanner.py`. Key changes:
- Fetches from `/events` endpoint with weather tag filtering
- Calls `GET /tags` on startup to discover weather tag IDs
- Returns raw market dicts with `event_id` for grouping
- Parses `clobTokenIds` (JSON string handling from existing code)
- Parses `outcomePrices` (JSON string handling from existing code)
- Stores `resolution_source` from API response

- [ ] **Step 3: Run tests, commit**

---

## Task 10: Market Question Parser

**Files:**
- Create: `polymarket_weather/markets/parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write comprehensive failing tests**

```python
import pytest
from polymarket_weather.markets.parser import parse_market_question, ParsedMarket

def test_range_fahrenheit():
    result = parse_market_question("Will the high temp in NYC be between 50 and 54 degrees Fahrenheit on April 15?")
    assert result is not None
    assert result.city == "nyc"
    assert result.metric == "temperature"
    assert result.threshold == 50.0
    assert result.threshold_upper == 54.0
    assert result.unit == "F"
    assert result.direction == "range"

def test_or_above():
    result = parse_market_question("Will the high temp in NYC be 55 degrees Fahrenheit or above on April 15?")
    assert result is not None
    assert result.threshold == 55.0
    assert result.direction == "above"

def test_or_below():
    result = parse_market_question("Will the high temp in NYC be 34 degrees Fahrenheit or below on April 15?")
    assert result is not None
    assert result.threshold == 34.0
    assert result.direction == "below"

def test_exceed():
    result = parse_market_question("Will the high temperature in Chicago exceed 80 degrees Fahrenheit on July 4?")
    assert result is not None
    assert result.city == "chicago"
    assert result.threshold == 80.0
    assert result.direction == "above"

def test_below_freezing():
    result = parse_market_question("Will NYC see temperatures below freezing on January 15?")
    assert result is not None
    assert result.threshold == 32.0  # freezing in F
    assert result.direction == "below"

def test_negative_temperature():
    result = parse_market_question("Will the low temperature in Denver drop to -5 degrees Fahrenheit?")
    assert result is not None
    assert result.threshold == -5.0

def test_celsius():
    result = parse_market_question("Will London high temperature be between 4 and 5 degrees Celsius on April 10?")
    assert result is not None
    assert result.unit == "C"
    assert result.threshold == 4.0
    assert result.threshold_upper == 5.0

def test_unparseable():
    result = parse_market_question("Who will win the 2026 Super Bowl?")
    assert result is None

def test_city_detection():
    from polymarket_weather.markets.parser import detect_city
    assert detect_city("highest temperature in new york city on april 5") == "new york city"
    assert detect_city("highest temperature in nyc on april 5") == "nyc"
    assert detect_city("highest temperature in los angeles on april 5") == "los angeles"
```

- [ ] **Step 2: Implement parser.py**

`ParsedMarket` dataclass:
```python
@dataclass
class ParsedMarket:
    city: str
    metric: str              # "temperature" | "precipitation" | "snow" | "wind"
    threshold: float
    threshold_upper: float | None  # For range markets
    unit: str                # "F" | "C"
    direction: str           # "above" | "below" | "range"
```

`parse_market_question(question: str) -> ParsedMarket | None` — layered regex patterns:
1. Range: "between X and Y degrees F/C"
2. "X degrees F or above/below"
3. "exceed/above/over X degrees"
4. "below/under X degrees"
5. Named thresholds: "freezing" → 32F/0C
6. Negative temperatures: `-\d+`

`detect_city(question: str) -> str | None` — matches against a configurable city alias list.

All patterns configurable via a `NAMED_THRESHOLDS` dict and city alias list loaded at init.

- [ ] **Step 3: Run tests, commit**

---

## Task 11: Forecast Engine

**Files:**
- Create: `polymarket_weather/weather/forecast.py`
- Create: `tests/test_forecast.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from polymarket_weather.weather.forecast import ForecastEngine, ForecastResult

def test_probability_above_threshold():
    """Given forecast mean=80F, std=3F, P(temp > 78) should be > 0.5"""
    from polymarket_weather.weather.forecast import compute_probability_above
    p = compute_probability_above(forecast_mean=80.0, sigma=3.0, threshold=78.0, df=7)
    assert 0.6 < p < 0.85

def test_probability_range():
    """Given forecast mean=52F, std=3F, P(50 <= temp <= 54) should be meaningful"""
    from polymarket_weather.weather.forecast import compute_probability_range
    p = compute_probability_range(forecast_mean=52.0, sigma=3.0, lower=50.0, upper=54.0, df=7)
    assert 0.2 < p < 0.6

def test_probability_range_far_from_mean():
    """Range far from mean should have low probability"""
    from polymarket_weather.weather.forecast import compute_probability_range
    p = compute_probability_range(forecast_mean=80.0, sigma=3.0, lower=50.0, upper=54.0, df=7)
    assert p < 0.01

def test_metar_trend_extrapolation():
    """Given rising temperatures, extrapolate forward"""
    from polymarket_weather.weather.forecast import metar_trend_forecast
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    readings = [
        (now - timedelta(hours=3), 10.0),
        (now - timedelta(hours=2), 11.0),
        (now - timedelta(hours=1), 12.0),
        (now, 13.0),
    ]
    target = now + timedelta(hours=2)
    mean, sigma = metar_trend_forecast(readings, target)
    assert 14.0 < mean < 16.0  # Continuing upward trend
    assert sigma > 0

def test_forecast_result_dataclass():
    result = ForecastResult(
        probability=0.72, confidence=0.85, source="metar",
        data_age_minutes=15.0, details={"readings": 4},
    )
    assert result.probability == 0.72
    assert result.source == "metar"
```

- [ ] **Step 2: Implement forecast.py**

Key functions:
- `compute_probability_above(forecast_mean, sigma, threshold, df) -> float` — `1 - t.cdf((threshold - mean) / sigma, df)`
- `compute_probability_range(forecast_mean, sigma, lower, upper, df) -> float` — `t.cdf((upper - mean) / sigma, df) - t.cdf((lower - mean) / sigma, df)`
- `metar_trend_forecast(readings: list[tuple[datetime, float]], target: datetime) -> tuple[float, float]` — linear regression on recent readings, returns (extrapolated_mean, estimated_sigma)

Class `ForecastEngine`:
- `__init__(config: ForecastConfig, session_factory, nwp_fetcher, city_mapper)`
- `async compute(station_id, metric, threshold, threshold_upper, direction, unit, resolution_at) -> ForecastResult | None`
  - Determines regime by hours_to_resolution
  - < metar_only_hours: uses `metar_trend_forecast` on recent readings from DB
  - metar_only_hours to blend_cutoff_hours: weighted blend of METAR trend + NWP
  - > blend_cutoff_hours: NWP ensemble via `nwp_fetcher`
  - Converts units (F↔C) as needed
  - Computes probability via t-distribution CDF
  - Returns `ForecastResult` with probability, confidence, source, details

All thresholds from config (metar_only_hours, blend_cutoff_hours, distribution_df, rmse_by_horizon, min_confidence).

- [ ] **Step 3: Run tests, commit**

---

## Task 12: Docker Compose + Integration Test

**Files:**
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `tests/test_integration.py`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: polymarket
      POSTGRES_PASSWORD: polymarket
      POSTGRES_DB: polymarket_weather
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U polymarket"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

- [ ] **Step 2: Write integration test**

A test that verifies the full pipeline: create config → init DB → insert station → collect mock METAR → run forecast. Uses the real PostgreSQL from Docker Compose.

- [ ] **Step 3: Run `docker compose up -d postgres` and test**

```bash
docker compose up -d postgres
DATABASE_URL="postgresql+asyncpg://polymarket:polymarket@localhost/polymarket_weather" python -m pytest tests/test_integration.py -v
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml Dockerfile tests/test_integration.py
git commit -m "feat: Docker Compose + integration test for data pipeline"
```

---

## Verification Checklist

After all tasks complete, verify:
- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `docker compose up -d postgres` — PostgreSQL starts
- [ ] `alembic upgrade head` — migrations apply cleanly
- [ ] Config loads from YAML with env overrides
- [ ] METAR parsing handles real aviationweather.gov JSON format
- [ ] NWP ensemble parsing handles Open-Meteo response format
- [ ] City mapper resolves all 20 seed cities
- [ ] Market parser handles range markets, above/below, named thresholds
- [ ] Forecast engine produces probabilities via t-distribution CDF
- [ ] All configurable parameters come from config.yaml (zero hardcoded values)