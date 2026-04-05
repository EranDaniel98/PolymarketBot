# Polymarket Weather Arbitrage System — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Approach:** Full rewrite — new weather core, adapted peripherals (Approach C)

## 1. Goal

Rewrite the Polymarket bot as a dedicated weather arbitrage system. The edge: aviation METAR weather data is accurate to +/-0.1C, freely available, and published every 1-3 hours. Polymarket weather markets are priced by retail sentiment, not sensor data. When these disagree significantly, we trade.

Remove all non-weather signal infrastructure (LLM, bookmaker, whale, crypto, social, news, polls, divergence, favorite-longshot). Remove cross-platform and structural arbitrage. Remove multi-signal decision engine.

## 2. Constraints

- **No hardcoded values** — every threshold, interval, URL, mapping, and parameter lives in `config.yaml` or `config/cities.json`
- **PostgreSQL** — single source of truth, replaces SQLite
- **React frontend** — full dashboard from day one
- **Paper trading first** — system must run in observation/paper mode before live trading

## 3. Project Structure

```
polymarket_weather/
├── weather/
│   ├── collector.py        # METAR + TAF polling from aviationweather.gov
│   ├── nwp.py              # Open-Meteo NWP ensemble fetching
│   ├── forecast.py         # P(condition met) calculator
│   └── city_mapper.py      # City -> ICAO station mapping
├── markets/
│   ├── scanner.py          # Polymarket weather market discovery via Gamma API
│   └── parser.py           # Question -> city, metric, threshold, direction, date
├── trading/
│   ├── mismatch.py         # Edge detection, EV calculation, opportunity ranking
│   ├── risk.py             # Position sizing (Kelly), limits, drawdown
│   ├── executor.py         # CLOB order placement (adapted from existing)
│   └── positions.py        # Position tracking, settlement detection, PnL
├── db/
│   ├── models.py           # SQLAlchemy ORM models (10 tables)
│   ├── session.py          # AsyncPG connection + session factory
│   └── queries.py          # Query helpers
├── alerts/
│   └── telegram.py         # Telegram notifications (adapted from existing)
├── api/
│   └── dashboard.py        # FastAPI endpoints
├── frontend/               # React + TypeScript + Tailwind + Recharts
│   ├── src/
│   │   ├── pages/          # Overview, Opportunities, Positions, History, etc.
│   │   ├── components/     # Shared UI components
│   │   └── api/            # API client hooks
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── config/
│   └── cities.json         # City -> ICAO seed data
├── migrations/             # Alembic migration scripts
├── scheduler.py            # APScheduler job definitions
├── config.py               # Dataclasses + YAML loading
├── event_bus.py            # Pub/sub (carried forward)
├── app.py                  # Main entrypoint
├── cli.py                  # Rich terminal dashboard
├── config.yaml
├── config.example.yaml
├── docker-compose.yml      # App + PostgreSQL
├── Dockerfile
└── pyproject.toml
```

## 4. Configuration System

All behavioral parameters in `config.yaml`. Environment variables override YAML for secrets. DB `risk_config` table overrides YAML for risk parameters (hot-reloadable via dashboard).

```yaml
polymarket:
  api_key: ""
  api_secret: ""
  private_key: ""
  chain_id: 137

database:
  url: "postgresql+asyncpg://user:pass@localhost:5432/polymarket_weather"

weather:
  metar:
    poll_interval: 1800
    stale_threshold: 10800
    api_url: "https://aviationweather.gov/api/data/metar"
    hours_lookback: 3
  taf:
    poll_interval: 21600
    api_url: "https://aviationweather.gov/api/data/taf"
  nwp:
    poll_interval: 21600
    api_url: "https://api.open-meteo.com/v1/forecast"
    models: ["gfs", "ecmwf_ifs025", "icon_global"]

forecast:
  metar_only_hours: 6
  blend_cutoff_hours: 30
  metar_blend_weight: 0.6
  min_confidence: 0.70
  long_range_min_confidence: 0.80
  long_range_days: 5

markets:
  scan_interval: 300
  discovery_interval: 900
  gamma_api_url: "https://gamma-api.polymarket.com/markets"
  weather_tags: ["weather", "temperature", "climate"]
  allowed_metrics: ["temperature"]

edge:
  min_edge: 0.12
  min_liquidity_usdc: 500
  min_confidence: 0.70
  min_hours_to_resolution: 2
  max_hours_to_resolution: 168
  kelly_fraction: 0.5

risk:
  max_position_usdc: 50
  min_trade_size_usdc: 5
  max_open_positions: 20
  daily_loss_cap_usdc: 200
  max_exposure_per_city_usdc: 150
  max_exposure_per_date_usdc: 200
  drawdown_pause_pct: 0.15

trading:
  order_type: "limit"
  slippage_tolerance: 0.02
  max_retries: 3
  exit_on_edge_inversion: true
  edge_inversion_threshold: -0.05
  paper_trading: true
  paper_balance: 1000
  cancel_before_resolution_minutes: 90

cities:
  file: "config/cities.json"

scheduler:
  metar_poll: 1800
  taf_poll: 21600
  nwp_poll: 21600
  market_scan: 300
  market_discovery: 900
  mismatch_detection: 300
  trade_execution: 60
  position_monitor: 120
  settlement_check: 600
  stale_data_check: 900
  daily_report: "08:00"
  calibration_update: "06:00"

notifications:
  telegram:
    enabled: true
    bot_token: ""
    chat_id: ""
    alert_on:
      opportunity_found: true
      trade_placed: true
      trade_settled: true
      risk_limit_approached: true
      data_stale: true
      system_error: true

logging:
  file_enabled: true
  file_path: "logs/bot.jsonl"
  max_size_mb: 50
  backup_count: 5

web:
  enabled: true
  host: "127.0.0.1"
  port: 8080

fee:
  default_taker_fee: 0.01
  maker_fee: 0.0
  weather_taker_fee: 0.01
```

### City Config (`config/cities.json`)

Separate file, editable without touching code:

```json
[
  {
    "city_aliases": ["new york", "nyc", "new york city"],
    "stations": ["KJFK", "KLGA", "KEWR"],
    "primary_station": "KJFK",
    "country": "US",
    "lat": 40.6413,
    "lon": -73.7781
  },
  {
    "city_aliases": ["tokyo"],
    "stations": ["RJTT", "RJAA"],
    "primary_station": "RJTT",
    "country": "JP",
    "lat": 35.5494,
    "lon": 139.7798
  }
]
```

## 5. Database Schema (PostgreSQL)

10 tables. SQLAlchemy async ORM with asyncpg. Alembic for migrations.

### icao_stations
| Column | Type | Notes |
|--------|------|-------|
| station_id | VARCHAR(10) | PK, ICAO code |
| city_name | VARCHAR(100) | IDX |
| country_code | CHAR(2) | ISO-3166 |
| lat | DECIMAL(8,5) | |
| lon | DECIMAL(8,5) | |
| elevation_m | INT | For altitude-adjusted temp |
| is_active | BOOLEAN | Whether we poll this station |
| last_report_at | TIMESTAMPTZ | Last successful METAR |
| reliability_score | DECIMAL(4,3) | 0-1, 30-day uptime |

### metar_readings
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| station_id | VARCHAR(10) | FK -> icao_stations |
| observed_at | TIMESTAMPTZ | IDX, from METAR |
| fetched_at | TIMESTAMPTZ | Our clock |
| temp_c | DECIMAL(5,1) | |
| dewpoint_c | DECIMAL(5,1) | |
| altim_hpa | DECIMAL(6,1) | |
| wind_speed_kt | INT | |
| visibility_m | INT | |
| raw_metar | TEXT | Full string for reprocessing |

Index: `(station_id, observed_at)`

### poly_markets
| Column | Type | Notes |
|--------|------|-------|
| market_id | VARCHAR(100) | PK, condition_id |
| question | TEXT | |
| city_name | VARCHAR(100) | IDX, parsed |
| station_id | VARCHAR(10) | FK, nullable until mapped |
| metric | VARCHAR(20) | temperature/rainfall/etc |
| threshold | DECIMAL(6,2) | |
| unit | VARCHAR(5) | C/F/mm/cm |
| direction | VARCHAR(10) | above/below/equal |
| resolution_at | TIMESTAMPTZ | IDX |
| yes_token_id | VARCHAR(100) | |
| no_token_id | VARCHAR(100) | |
| status | VARCHAR(20) | active/resolved/cancelled |
| resolution_source | TEXT | Who settles it |

### opportunities
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| market_id | VARCHAR(100) | FK -> poly_markets |
| detected_at | TIMESTAMPTZ | IDX |
| our_p | DECIMAL(5,4) | |
| market_p | DECIMAL(5,4) | |
| edge | DECIMAL(5,4) | our_p - market_p |
| direction | VARCHAR(5) | YES or NO |
| confidence | DECIMAL(4,3) | |
| forecast_source | VARCHAR(30) | metar/metar_taf/nwp_ensemble |
| forecast_snapshot | JSONB | Full data for audit |
| traded | BOOLEAN | |
| skip_reason | VARCHAR(50) | risk_limit/low_liquidity/etc |

### trades
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| opportunity_id | BIGINT | FK -> opportunities |
| poly_order_id | VARCHAR(100) | IDX |
| token_id | VARCHAR(100) | |
| size_usdc | DECIMAL(12,2) | |
| limit_price | DECIMAL(5,4) | |
| fill_price | DECIMAL(5,4) | Null if pending |
| fill_size_usdc | DECIMAL(12,2) | |
| status | VARCHAR(20) | pending/partial/filled/cancelled/settled |
| placed_at | TIMESTAMPTZ | |
| settled_at | TIMESTAMPTZ | |
| settlement_result | VARCHAR(10) | YES/NO |
| pnl_usdc | DECIMAL(12,2) | Net after fees |
| exit_reason | VARCHAR(30) | settlement/manual/stop_loss/edge_inverted |

### city_icao_mapping
| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL | PK |
| city_pattern | VARCHAR(100) | Polymarket city name/pattern |
| station_id | VARCHAR(10) | FK -> icao_stations |
| priority | INT | Higher = preferred |
| created_at | TIMESTAMPTZ | |

Manual overrides — takes precedence over cities.json auto-mapping.

### forecast_snapshots
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| station_id | VARCHAR(10) | FK |
| created_at | TIMESTAMPTZ | IDX |
| source | VARCHAR(20) | metar/taf/nwp |
| model_name | VARCHAR(30) | gfs/ecmwf/icon |
| forecast_data | JSONB | Raw forecast data |

### edge_calibration
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| opportunity_id | BIGINT | FK -> opportunities |
| our_p | DECIMAL(5,4) | |
| actual_outcome | BOOLEAN | |
| forecast_source | VARCHAR(30) | |
| resolved_at | TIMESTAMPTZ | IDX |

### risk_config
| Column | Type | Notes |
|--------|------|-------|
| key | VARCHAR(50) | PK |
| value | TEXT | Stored as string, cast on read |
| updated_at | TIMESTAMPTZ | |

Hot-reloadable. DB values override YAML defaults.

### system_events
| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL | PK |
| event_type | VARCHAR(50) | IDX |
| severity | VARCHAR(10) | info/warning/error/critical |
| message | TEXT | |
| details | JSONB | |
| created_at | TIMESTAMPTZ | IDX |

## 6. Core Weather Modules

### Weather Collector (`weather/collector.py`)

Polls aviationweather.gov for METAR and TAF data on two schedules.

**METAR polling:**
- Bulk fetch by station IDs for all active stations
- Parses JSON response (structured, not raw METAR strings)
- Stores each observation in `metar_readings`
- Deduplicates on (station_id, observed_at)
- Updates `icao_stations.last_report_at`
- Detects stale stations (last_report_at > stale_threshold) -> emits `station_stale` event
- Updates `reliability_score` — rolling 30-day uptime

**TAF polling:**
- Fetches terminal aerodrome forecasts every 6h
- Covers 24-30h forecast horizon
- Stored in `forecast_snapshots`

### NWP Fetcher (`weather/nwp.py`)

Fetches numerical weather predictions from Open-Meteo for markets resolving 30h-7d out.

- Queries multiple models (configurable: GFS, ECMWF, ICON) per city lat/lon
- Returns hourly temperature forecasts for the resolution window
- Stores raw responses in `forecast_snapshots`
- Respects Open-Meteo 10k calls/day free tier — batches by city, caches aggressively

### Forecast Engine (`weather/forecast.py`)

Computes P(condition met) with confidence interval. Three regimes based on hours to resolution:

**< metar_only_hours (default 6h):** METAR trend model
- Last N readings for the station
- Linear trend extrapolation to resolution time
- P(threshold crossed) from trend + historical variance at that station (computed from `metar_readings` history; requires minimum 24h of readings before producing forecasts for a station)
- Highest confidence — ground truth sensor data

**metar_only_hours to blend_cutoff_hours (default 6-30h):** METAR + TAF blend
- METAR trend as above
- TAF forecast parsed for resolution window
- Weighted blend (configurable, default 0.6 METAR / 0.4 TAF)
- Wider confidence interval

**> blend_cutoff_hours (default 30h+):** NWP ensemble
- All configured NWP models queried for resolution time
- Ensemble mean = probability estimate
- Ensemble spread = confidence interval
- If CI below min_confidence -> opportunity skipped

Returns:
```python
@dataclass
class ForecastResult:
    probability: float        # P(condition met), 0-1
    confidence: float         # CI confidence, 0-1
    source: str               # "metar" | "metar_taf" | "nwp_ensemble"
    data_age_minutes: float   # Age of freshest data point
    details: dict             # Full data for audit snapshot
```

## 7. Market Discovery & Mismatch Detection

### Market Scanner (`markets/scanner.py`)

Two jobs:
- **Discovery** (every 15 min): Gamma API for new weather markets, run parser, create `poly_markets` rows
- **Price scan** (every 5 min): CLOB API midpoint prices for all active markets

### Market Parser (`markets/parser.py`)

Extracts structured data from question text via layered regex patterns:

Input: `"Will the high temperature in New York exceed 80 degrees F on April 15?"`
Output: `ParsedMarket(city="new york", metric="temperature", threshold=80.0, unit="F", direction="above", resolution_date=date(2026, 4, 15))`

Patterns for:
- Temperature ranges: "40-45 degrees F", "between 55 and 60", "above 80", "below freezing"
- Metrics: temperature, high temp, low temp, rainfall, snowfall, wind
- Cities: matched against aliases from cities.json
- Dates: "on April 15", "this Saturday", "tomorrow"
- Units: F, C, mm, inches

Unparseable markets flagged with `station_id = NULL` for manual mapping via dashboard.

### City Mapper (`weather/city_mapper.py`)

Resolution order:
1. `city_icao_mapping` DB table (manual overrides, highest priority)
2. `cities.json` aliases
3. No match -> flagged as unmapped in dashboard

### Mismatch Detector (`trading/mismatch.py`)

Runs every 5 min. For each active, mapped market:

1. Forecast engine -> `our_p`
2. Current market price -> `market_p`
3. `edge = our_p - market_p`
4. `ev = edge * (1 - market_p)`
5. `kelly_f = edge / (1 - market_p)` * configured fraction
6. Filter checks (all from config):
   - `|edge| >= min_edge`
   - Liquidity >= `min_liquidity_usdc`
   - Confidence >= `min_confidence`
   - Hours to resolution in [min_hours, max_hours]
   - Station not stale
   - No duplicate position on same market
7. Positive edge -> buy YES; our_p significantly below market_p -> buy NO
8. Log to `opportunities` (every mismatch, traded or not)
9. Passing opportunities emitted as `opportunity` events

Mismatch detection and trade execution are **decoupled** via event bus. Never combined into one job.

## 8. Trading & Risk

### Risk Manager (`trading/risk.py`)

Pre-trade checks (all must pass, all from config):
- `size <= max_position_usdc`
- `open_positions < max_open_positions`
- `city_exposure < max_exposure_per_city_usdc`
- `date_exposure < max_exposure_per_date_usdc`
- `daily_losses < daily_loss_cap_usdc`
- No duplicate position on same market
- Station not stale for this city
- `drawdown_from_peak < drawdown_pause_pct`

Position sizing:
```
raw_kelly = edge / (1 - market_price)
size = bankroll * raw_kelly * kelly_fraction
size = clamp(size, min_trade_size_usdc, max_position_usdc)
```

Rejected opportunities logged to `opportunities.skip_reason`.

### Trade Executor (`trading/executor.py`)

Adapted from existing `ExecutionEngine`:
- **Kept:** py-clob-client CLOB auth/signing, paper trading, limit orders, repricing loop, slippage guard, order book depth check, retry with backoff
- **Removed:** structural arb methods, arb leg monitoring
- **Added:** cancel open orders before resolution (configurable minutes), opportunity_id FK on trades

### Position Manager (`trading/positions.py`)

Runs every 2 min:
- Fetches current price per open position
- Recomputes edge (re-runs forecast vs current market price)
- Exit on edge inversion beyond threshold
- Settlement check every 10 min via Polymarket API
- Records settlement_result, pnl_usdc on trades
- Feeds into edge_calibration table
- Emits trade_settled events

PnL:
```
YES position: pnl = ((1.0 if settled YES else 0.0) - fill_price) * fill_size - fees
NO position:  pnl = ((1.0 if settled NO else 0.0) - fill_price) * fill_size - fees
```

## 9. Scheduler

APScheduler. All intervals from config.

| Job | Default | Description |
|-----|---------|-------------|
| metar_poll | 30 min | Fetch METAR for all active stations |
| taf_poll | 6 hours | Refresh TAF forecasts |
| nwp_poll | 6 hours | Fetch NWP for markets >30h out |
| market_scan | 5 min | Update prices on active markets |
| market_discovery | 15 min | Gamma API for new markets |
| mismatch_detection | 5 min | Edge calculation, queue opportunities |
| trade_execution | 1 min | Process queue, risk check, place orders |
| position_monitor | 2 min | Check positions, trigger exits |
| settlement_check | 10 min | Detect resolved markets, record PnL |
| stale_data_check | 15 min | Alert + pause on stale stations |
| daily_report | 08:00 UTC | PnL summary to Telegram |
| calibration_update | 06:00 UTC | Re-run calibration from last 30 days |

## 10. Alerts

Telegram bot adapted from existing code. Message types:
- Opportunity found (city, our P, market P, edge)
- Trade placed (direction, size, price, question)
- Trade settled (win/loss, PnL, cumulative)
- Risk limit approached (which limit, current vs threshold)
- Station stale (station, last report, affected markets)
- System error (scheduler failures, API errors)
- Daily report (PnL, positions, win rate, bankroll)

Each type independently toggleable in config.

## 11. Dashboard

### Backend (FastAPI)

| Endpoint | Data |
|----------|------|
| GET /api/overview | PnL, positions count, trades today, win rate, status |
| GET /api/opportunities | Live mismatches — city, our_p, market_p, edge, liquidity |
| GET /api/positions | Open trades — entry/current price, unrealized PnL |
| GET /api/history | Settled trades, filterable by city/date/outcome |
| GET /api/weather | Last METAR per station, staleness indicator |
| GET /api/calibration | our_p vs actual outcome by decile |
| GET /api/config | Read risk parameters |
| PUT /api/config | Write risk parameters to DB |
| GET /api/cities | City-ICAO mapping, unmapped flagged |
| PUT /api/cities | Update mapping |
| GET /api/events | System event log, filterable by severity |

### Frontend (React + TypeScript)

Stack: React, TypeScript, Tailwind CSS, Recharts, Vite

Pages:
- **Overview** — PnL cards, system status badges, quick stats
- **Live Opportunities** — auto-refreshing table (5s), sortable columns
- **Open Positions** — real-time unrealized PnL, edge at entry vs now
- **Trade History** — paginated, filterable, CSV export
- **Weather Monitor** — station cards, color-coded staleness
- **Edge Calibration** — Recharts scatter: our_p vs actual by decile
- **Config Editor** — form reading/writing /api/config
- **City Mapping** — table with unmapped highlighted, inline edit
- **System Logs** — severity-filtered event stream

Served by FastAPI (static files in production), Vite dev server in development.

### CLI Dashboard

Rich terminal dashboard: system status, open positions with PnL, recent opportunities, recent trades, station health, data freshness.

## 12. Adapted Peripherals

### From existing bot — adapted, not rewritten:

**ExecutionEngine:** CLOB client init, order signing via py-clob-client, paper trading simulation, limit order placement, repricing loop, slippage guard, order book depth check, retry with backoff. Stripped of all arb-related code.

**TelegramNotifier:** Bot token management, callback query handling, inline approve/reject buttons, message sending. Templates updated for weather-specific content.

**EventBus:** Pub/sub pattern carried forward unchanged.

**Config loader:** YAML + env override pattern. New dataclass hierarchy for weather-specific config.

## 13. Data Flow Summary

```
aviationweather.gov ─── METAR/TAF ───> Weather Collector ───> metar_readings DB
                                                          ├──> icao_stations (staleness)
                                                          └──> forecast_snapshots

Open-Meteo ─── NWP forecasts ───> NWP Fetcher ───> forecast_snapshots DB

Gamma API ─── market list ───> Market Scanner ───> Market Parser ───> poly_markets DB
CLOB API  ─── prices ───────> Market Scanner ───────────────────────> poly_markets DB

Forecast Engine <── metar_readings + forecast_snapshots
       │
       └──> ForecastResult(probability, confidence, source)
                │
                v
Mismatch Detector <── poly_markets (current price)
       │
       ├──> opportunities DB (every mismatch)
       └──> [opportunity event] ───> Trade Executor
                                        │
                                        ├──> Risk Manager (pre-trade checks)
                                        ├──> CLOB API (order placement)
                                        └──> trades DB
                                               │
Position Manager <── trades + CLOB API ────────┘
       │
       ├──> edge_calibration DB (on settlement)
       ├──> [trade_settled event] ───> Telegram
       └──> Exit triggers (edge inversion, settlement)
```

## 14. Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Scheduler | APScheduler |
| HTTP client | httpx (async) |
| DB ORM | SQLAlchemy 2.0 (async) + asyncpg |
| Migrations | Alembic |
| Data models | Pydantic + dataclasses |
| Statistics | scipy, numpy |
| Geo matching | geopy (if needed for auto-mapping) |
| Polymarket auth | py-clob-client |
| Dashboard API | FastAPI |
| Dashboard UI | React + TypeScript + Tailwind + Recharts + Vite |
| Alerts | python-telegram-bot |
| Deployment | Docker Compose (app + PostgreSQL) |
| Secrets | .env + python-dotenv |
| Logging | structlog -> JSON |
