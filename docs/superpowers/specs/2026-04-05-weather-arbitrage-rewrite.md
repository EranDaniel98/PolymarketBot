# Polymarket Weather Arbitrage System — Design Spec

**Date:** 2026-04-05
**Updated:** 2026-04-05 (v2 — incorporates 20-agent review)
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

---

## 15. Review Corrections (v2 — 20-agent review)

All findings below override the corresponding sections above. These are organized by severity.

### 15.1 SPEC-BREAKING: Multi-Outcome Markets

Polymarket weather markets are **NOT binary above/below**. They are **multi-outcome events with discrete 2°F/2°C temperature buckets**:

```
Event: "NYC High Temperature April 15"
  Market 1: "...between 35 and 39°F?" → YES/NO (conditionId: 0xaaa)
  Market 2: "...between 40 and 44°F?" → YES/NO (conditionId: 0xbbb)
  Market 3: "...between 45 and 49°F?" → YES/NO (conditionId: 0xccc)
  Market 4: "...50°F or above?"        → YES/NO (conditionId: 0xddd)
  Market 5: "...34°F or below?"        → YES/NO (conditionId: 0xeee)
```

**Impact on design:**
- `poly_markets` table needs `event_id VARCHAR(100)` and `group_id VARCHAR(100)` columns to link sibling markets
- The Mismatch Detector must operate on **event groups**, not individual markets: compute P(temp in each range) from the forecast distribution, compare each range's probability against its market price
- Sum-to-one validation: all range prices within an event should sum to ~1.0; significant deviation = structural mispricing opportunity
- Hedging: buy the forecasted range, optionally short adjacent ranges
- Risk: `max_exposure_per_city_usdc` applies to the **event group**, not individual range markets

### 15.2 SPEC-BREAKING: TAF Has No Temperature for US Cities

**US NWS TAFs contain NO temperature forecasts.** The 0.6 METAR / 0.4 TAF blend for the 6-30h window is fundamentally broken for US cities (the majority of Polymarket weather markets).

International TAFs include only TX/TN (daily max/min), not hourly temperatures. These are insufficient for the spec's probability calculation.

**Revised forecast regimes:**

| Hours to resolution | Old spec | Corrected |
|---------------------|----------|-----------|
| 0-6h | METAR trend only | **METAR trend only** (unchanged) |
| 6-12h | 0.6 METAR + 0.4 TAF | **METAR trend (decaying weight) + NWP ensemble (increasing weight)**. E.g., 0.8/0.2 at 6h → 0.3/0.7 at 12h |
| 12-30h | 0.6 METAR + 0.4 TAF | **NWP ensemble primary**. METAR trend is useless at 24h+ (diurnal cycle dominates) |
| 30h-7d | NWP ensemble | **NWP ensemble** (unchanged) |

TAF remains in the system for: (a) non-temperature weather markets (precipitation/wind), (b) sanity-checking NWP output, (c) future expansion.

### 15.3 SPEC-BREAKING: NWP Probability Conversion

The spec says "ensemble mean = probability estimate." **This is fundamentally wrong.** NWP models output **temperature point forecasts**, not probabilities. You cannot use the mean of 3 temperatures as a probability.

**Corrected approach:**

1. Get forecast temperature distribution: either from Open-Meteo `/v1/ensemble` endpoint (51 ECMWF members), or from multiple deterministic models
2. Compute sigma (uncertainty): `sigma = f(lead_time, ensemble_spread, historical_RMSE)`
3. Assume distribution: **Student's t-distribution with df=5-7** (better tail behavior than normal for weather)
4. Compute probability via CDF: `P(temp in range [a,b]) = F_t((b - forecast_mean) / sigma) - F_t((a - forecast_mean) / sigma)`

**Use Open-Meteo `/v1/ensemble` endpoint** (NOT the deterministic `/v1/forecast`):
- ECMWF ENS: 51 ensemble members → real probability distribution
- Endpoint: `https://api.open-meteo.com/v1/ensemble`
- Each member produces an independent temperature forecast
- Spread directly provides uncertainty, no RMSE lookup needed

Add RMSE lookup table to config for fallback:
```yaml
forecast:
  rmse_by_horizon:
    6h: 1.5    # degrees C
    12h: 2.0
    24h: 2.5
    48h: 3.0
    72h: 3.5
    120h: 4.0
    168h: 4.5
  distribution_df: 7  # Student's t degrees of freedom
```

### 15.4 SPEC-BREAKING: Resolution Source

Polymarket resolves temperature markets via **Weather Underground** (station data), NOT NOAA. The system must:
- Track the resolution source per market from the Gamma API `resolutionSource` field
- Calibrate forecasts against Weather Underground readings, not just raw METAR
- Note: WU data can diverge from METAR (documented cases of discrepancies in Shenzhen)

### 15.5 SPEC-BREAKING: METAR API Field Names

All field names in the spec are wrong. Actual aviationweather.gov JSON:

| Spec says | Actual field | Type | Notes |
|-----------|-------------|------|-------|
| `temp_c` | `temp` | float | Celsius |
| `dewpoint_c` | `dewp` | float | Celsius |
| `altim_hpa` | `altim` | float | hPa |
| `wind_speed_kt` | `wspd` | int | Knots |
| `visibility_m` | `visib` | string | **Statute miles, NOT meters**. Can be `"10+"` |
| `obs_time` | `obsTime` | int | **Unix epoch**, not ISO string |

Additional fields to store (critical for temperature forecasting):
- `wdir` (int) — wind direction degrees, affects temperature advection
- `wgst` (int|null) — wind gust knots
- `clouds` (array) — `[{"cover": "BKN", "base": 60}]` — **most important missing field** for temperature forecasting (cloud cover suppresses diurnal range)
- `wxString` (string|null) — weather phenomena (rain, fog, snow)
- `slp` (float) — sea level pressure
- `metarType` (string) — METAR vs SPECI (special reports triggered by weather changes)

**Remarks T-group** (`Txxxxxxxx` in `rawOb`): provides temperature to **0.1°C precision** vs 1°C in the main body. Must parse this — 10x better resolution for trend detection.

Update `metar_readings` schema:
```sql
ADD COLUMN wind_dir_deg     INT
ADD COLUMN wind_gust_kt     INT
ADD COLUMN cloud_cover      JSONB        -- [{cover, base}]
ADD COLUMN wx_string        VARCHAR(50)
ADD COLUMN slp_hpa          DECIMAL(6,1)
ADD COLUMN metar_type       VARCHAR(10)  -- METAR or SPECI
ADD COLUMN temp_precise_c   DECIMAL(5,1) -- From remarks T-group (0.1C resolution)
-- Rename visibility_m to visib_sm (statute miles, stored as text)
```

### 15.6 SPEC-BREAKING: Kelly Criterion Formulas

**EV formula is wrong:**
- Spec: `ev = edge * (1 - market_price)` — INCORRECT
- Correct: `ev = edge` (EV per dollar wagered)

**NO-side Kelly missing:**
- YES side: `kelly_f = (our_p - market_price) / (1 - market_price)` ✓
- NO side: `kelly_f = (market_price - our_p) / market_price`

**Edge case capping needed:**
- Clamp `market_price` to `[0.05, 0.95]` for Kelly calculations
- Cap `raw_kelly` at 0.25 before fractional multiplier

**Fee adjustment missing:**
- For YES: `b = (1 - market_price) / market_price * (1 - fee)`
- Incorporate fee into the odds before Kelly calculation

### 15.7 Missing Risk Parameters

Add to `risk` config section:
```yaml
risk:
  max_total_exposure_usdc: 600    # CRITICAL — prevents 20*$50 = full bankroll
  max_exposure_per_region_usdc: 250  # Geographic correlation (NYC/Newark/Philly)
  cooldown_after_exit_seconds: 1800  # One METAR cycle between re-entry
  bootstrap_trades: 50              # Conservative fixed sizing while model unproven
  bootstrap_size_usdc: 10           # $10/trade during learning phase
  max_forecast_age_minutes: 30      # Reject stale probabilities
  drawdown_recovery_mode: "auto"    # auto | manual
  drawdown_recovery_hours: 4
  drawdown_recovery_sizing_pct: 0.50
```

Tiered `min_edge` by forecast source:
```yaml
edge:
  min_edge_metar: 0.06        # Sensor data edge — tighter spreads expected
  min_edge_blend: 0.08        # Transitional window
  min_edge_nwp: 0.12          # NWP ensemble — need larger margin
  min_liquidity_usdc: 200     # 200 for paper trading (raise to 500 for live)
  cancel_before_resolution_minutes: 120  # Increased from 90
```

Add regions to `cities.json`:
```json
{
  "city_aliases": ["new york", "nyc"],
  "stations": ["KJFK", "KLGA", "KEWR"],
  "primary_station": "KJFK",
  "region": "northeast_us",
  "country": "US",
  "lat": 40.6413,
  "lon": -73.7781
}
```

### 15.8 Open-Meteo API Corrections

- Model name: `gfs` → `gfs_seamless`
- Use `/v1/ensemble` endpoint for probability calculation (51 ECMWF members)
- Multi-model in one request: `?models=gfs_seamless,ecmwf_ifs025,icon_global`
- Rate sub-limits: 5,000/hour, 600/minute (not just 10k/day)
- ECMWF horizon: 10 days (not 16). ICON: 7.5 days. Only GFS reaches 16.
- Response uses model-prefixed field names when multiple models requested: `temperature_2m_gfs_seamless`

### 15.9 Gamma API Corrections

- Weather tags are **numeric IDs**, not strings. Must call `GET /tags` first to discover tag IDs
- Use `/events` endpoint (not `/markets`) for discovery — groups related markets together
- `clobTokenIds` is a JSON-encoded string, not separate fields
- Use `tag_slug` on `/events` for string-based filtering (alternative to numeric `tag_id`)
- Config change:
```yaml
markets:
  discovery_endpoint: "/events"   # Not /markets
  weather_tag_discovery: true     # Auto-discover tag IDs via GET /tags
  fallback_keywords: ["temperature", "weather", "degrees", "high temp"]
```

### 15.10 Database Index Additions

Add these indexes (missing from original spec):

| Table | Index | Rationale |
|-------|-------|-----------|
| `metar_readings` | **UNIQUE** `(station_id, observed_at)` | Enforce dedup at DB level, enable upserts |
| `trades` | `(status, placed_at)` | Position manager queries open trades constantly |
| `trades` | `(opportunity_id)` | FK lookups for PnL reporting |
| `opportunities` | `(market_id, detected_at)` | Duplicate detection |
| `opportunities` | PARTIAL `WHERE traded = false` on `(traded)` | Dashboard live opportunities |
| `forecast_snapshots` | `(station_id, created_at)` | Latest snapshot per station |
| `edge_calibration` | `(forecast_source, resolved_at)` | Calibration grouping |
| `poly_markets` | PARTIAL `WHERE status = 'active'` on `(status)` | Most queries filter active only |
| `city_icao_mapping` | `(city_pattern)` | Lookup on every parse |
| `system_events` | `(severity, created_at)` | Dashboard filtering |

### 15.11 Edge Calibration Enhancements

**Daily calibration job outputs:** Brier score, ECE, Resolution, Log Loss, Sharpness — per forecast_source and global.

**Correction mechanism:** Isotonic regression per forecast_source (not Platt scaling). Requires 50+ samples to activate. Clamp correction to +/-0.15. Config flag to disable.

**Additional columns for `edge_calibration`:**
- `station_id VARCHAR(10)` — denormalized for query efficiency
- `hours_to_resolution DECIMAL(6,1)` — at time of prediction
- `month SMALLINT` — for seasonal analysis
- `edge_at_entry DECIMAL(5,4)` — our_p - market_p at trade time
- `calibrated_p DECIMAL(5,4)` — post-correction probability

Add calibration config:
```yaml
calibration:
  lookback_days: 30
  min_samples_for_reporting: 30
  min_samples_for_correction: 50
  min_samples_per_bin: 10
  apply_correction: false     # Enable manually once enough data
  max_correction: 0.15        # Clamp adjustment
```

### 15.12 Market Parser Improvements

**Expand regex patterns** from 6 to ~18, covering:
- "X degrees Fahrenheit or above/below" (Polymarket's actual format)
- Negative temperatures (`-5°F`)
- Decimal thresholds (`32.5`)
- Named thresholds (`freezing` = 32°F/0°C, `boiling` = 212°F/100°C)
- Multiple unit spellings ("Fahrenheit", "F", "°F", "degrees F")

**Use API `endDate` as primary resolution date** — do not rely on regex date extraction.

**Add `event_id` to `poly_markets`** for multi-outcome grouping.

**LLM fallback** for ~5-10% parse failure tail:
- Cheap model (GPT-4o-mini / Haiku) for structured extraction
- Cache results by question text
- Track regex-fail rate in `system_events`

**Normalize internally to Celsius** since METAR data is in C. Convert market thresholds from F to C for comparison.

### 15.13 APScheduler 4.x

Use `AsyncScheduler` (APScheduler 4.x), NOT `AsyncIOScheduler` (3.x):
- `CoalescePolicy.latest` for overlap protection
- `CronTrigger` for daily jobs (parsed from config time strings)
- `JobReleased` event subscriber for error handling → Telegram alerts
- **Combine `market_scan` + `mismatch_detection`** into one sequential job (both 5-min interval)
- DB-backed opportunity queue via `opportunities` table (not in-memory `asyncio.Queue`)
- Optional: `SQLAlchemyDataStore` for schedule persistence across restarts

### 15.14 Frontend Stack

| Concern | Library |
|---------|---------|
| UI components | **shadcn/ui** (Radix + Tailwind) |
| Charts | **Recharts** (via shadcn/ui Chart component) |
| Server state / polling | **TanStack Query v5** (`refetchInterval`) |
| Tables | **TanStack Table v8** (via shadcn/ui DataTable) |
| Forms | **React Hook Form + Zod** (via shadcn/ui Form) |
| Client state | `useState`/`useContext` (add Zustand if needed) |

### 15.15 FastAPI Patterns

- **Shared `async_sessionmaker` factory** — each endpoint and scheduled job gets its own `AsyncSession`
- **Hot-reload config:** `PUT /api/config` writes to DB, publishes `config_changed` on EventBus, RiskManager subscribes and reloads
- **WebSocket endpoint** `ws://localhost:8080/ws/live` for real-time dashboard data (backed by EventBus subscriptions)
- **SPA serving:** `app.mount("/", StaticFiles(directory="frontend/dist", html=True))` after API routes
- **Pydantic models** for all request/response validation
- **CORS** only in dev mode (Vite proxy preferred)

### 15.16 Security

**CRITICAL — Rotate all API keys.** Existing `config.yaml` has plaintext secrets that may be in git history.

Requirements for the new system:
- All secrets in `.env` only (never in YAML). Actually call `load_dotenv()` in `app.py`
- Ship `.env.example` with all variable names (empty values)
- `DATABASE_URL` in env vars, never in YAML
- Extend `_SecretStr` to all secret fields (or use `pydantic.SecretStr`)
- **Dashboard auth mandatory** — refuse to start web server without `DASH_PASS`
- Add CSRF protection on write endpoints (`PUT /api/config`, `PUT /api/cities`)
- Add structlog processor to scrub secret patterns from logs
- Docker: use `env_file`, add `.env` to `.dockerignore`

### 15.17 METAR Trend Model Improvement

Linear extrapolation is fragile — the diurnal temperature cycle is sinusoidal. Improvements:
- Fit a sinusoidal/spline model rather than linear
- Weight recent observations more heavily (exponential weighting)
- Include cloud cover as a feature (overcast suppresses cooling/heating)
- Detect regime changes (SPECI reports signal rapid weather shifts)
- Use pressure tendency from remarks to detect approaching fronts
- Require minimum 24h of readings before producing forecasts for a station

### 15.18 py-clob-client Notes

- `get_balance_allowance()` must pass `BalanceAllowanceParams()` (not called with no args)
- Batch APIs available: `get_prices()`, `get_midpoints()` for bulk price fetching — use in market_scan job
- Heartbeat mechanism (`post_heartbeat()`) available for safety — auto-cancels all orders if heartbeat missed
- SDK is v0.34.6, all methods used by ExecutionEngine are confirmed present

### 15.19 Carry-Forward Code Map

| Module | Action | Key methods to keep |
|--------|--------|-------------------|
| `execution/engine.py` | Adapt | `start`, `stop`, `get_balance`, `_place_order`, `execute`, `_reprice_loop`, `check_order_book_depth`. Delete all arb methods |
| `notifications/telegram.py` | Adapt | Entire class. Update signal summary in `_send_approval_message` |
| `event_bus.py` | Keep | Unchanged |
| `notifications/base.py` | Keep | `Notifier` ABC, `NotificationLevel` |
| `scanner.py` | Adapt | `MarketScanner` class, `_parse_market` (Gamma API parsing). Add weather tag filter |
| `exit_manager.py` | Adapt | `ExitManager`, `ExitRule`, `TrackedPosition`. All position tracking + exit logic |
| `cli.py` | Adapt | All formatting utilities, status/position/trade panels. Rewrite signal and plugin panels for weather |

### 15.20 Additional Config Corrections

```yaml
weather:
  metar:
    api_url: "https://aviationweather.gov/api/data/metar"
    user_agent: "PolymarketWeatherBot/1.0"  # Required to avoid blocks
    max_results_per_request: 400             # API limit
  nwp:
    api_url: "https://api.open-meteo.com/v1/ensemble"  # Ensemble endpoint, not /forecast
    models: ["ecmwf_ifs025"]                             # 51 ensemble members
    deterministic_models: ["gfs_seamless", "ecmwf_ifs025", "icon_global"]  # Fallback
    rate_limit_per_minute: 600
    rate_limit_per_hour: 5000
```
