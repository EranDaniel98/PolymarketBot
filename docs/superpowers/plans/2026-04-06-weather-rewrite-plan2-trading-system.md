# Weather Arbitrage Rewrite — Plan 2: Trading System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the trading engine that turns forecast probabilities into trades — mismatch detection, risk management, order execution, position tracking, settlement, alerts, scheduling, and CLI dashboard. When complete, the bot runs end-to-end in paper trading mode.

**Architecture:** Mismatch detector compares forecast P vs market price, applies Kelly sizing. Risk manager enforces all limits. Executor adapted from existing py-clob-client code. APScheduler 4.x orchestrates all jobs. Telegram alerts for trades/errors. Rich CLI dashboard for monitoring.

**Tech Stack:** APScheduler 4.x, py-clob-client, python-telegram-bot, Rich, structlog

**Depends on:** Plan 1 (data pipeline) — all modules in `polymarket_weather/weather/`, `polymarket_weather/markets/`, `polymarket_weather/db/`

**Spec:** `docs/superpowers/specs/2026-04-05-weather-arbitrage-rewrite.md` (v2)

---

## File Map

```
polymarket_weather/
├── trading/
│   ├── __init__.py           (exists)
│   ├── mismatch.py           # Edge detection + opportunity ranking
│   ├── risk.py               # Position sizing, limits, drawdown
│   ├── executor.py           # CLOB order placement (adapted)
│   └── positions.py          # Position tracking, settlement, PnL
├── alerts/
│   ├── __init__.py           (exists)
│   └── telegram.py           # Telegram notifications (adapted)
├── scheduler.py              # APScheduler 4.x job orchestration
├── app.py                    # Main entrypoint — wires everything together
├── cli.py                    # Rich terminal dashboard
tests/
├── test_mismatch.py
├── test_risk.py
├── test_executor.py
├── test_positions.py
├── test_scheduler.py
```

---

## Task 1: Mismatch Detector

**Files:**
- Create: `polymarket_weather/trading/mismatch.py`
- Create: `tests/test_mismatch.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from polymarket_weather.trading.mismatch import (
    compute_edge, compute_kelly_size, filter_opportunity, OpportunitySignal,
)

def test_compute_edge_yes():
    """Our P > market P → positive edge, direction YES"""
    edge = compute_edge(our_p=0.75, market_p=0.55)
    assert edge.raw_edge == pytest.approx(0.20)
    assert edge.direction == "YES"
    assert edge.ev == pytest.approx(0.20)  # EV = edge (corrected formula)

def test_compute_edge_no():
    """Our P < market P → buy NO"""
    edge = compute_edge(our_p=0.30, market_p=0.60)
    assert edge.direction == "NO"
    assert edge.raw_edge == pytest.approx(0.30)  # |market_p - our_p|

def test_compute_edge_no_edge():
    edge = compute_edge(our_p=0.55, market_p=0.55)
    assert edge.raw_edge == pytest.approx(0.0)

def test_kelly_size_yes():
    """Kelly for YES: edge / (1 - market_price) * fraction * bankroll"""
    size = compute_kelly_size(
        edge=0.20, market_price=0.55, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert 5 <= size <= 50

def test_kelly_size_no():
    """Kelly for NO: edge / market_price * fraction * bankroll"""
    size = compute_kelly_size(
        edge=0.20, market_price=0.60, direction="NO",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert 5 <= size <= 50

def test_kelly_size_clamped_max():
    size = compute_kelly_size(
        edge=0.50, market_price=0.30, direction="YES",
        bankroll=10000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 50  # Clamped to max

def test_kelly_size_below_min():
    size = compute_kelly_size(
        edge=0.001, market_price=0.50, direction="YES",
        bankroll=100, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 0  # Below min → don't trade

def test_kelly_extreme_price_clamped():
    """Market price near 1.0 should be clamped to avoid blow-up"""
    size = compute_kelly_size(
        edge=0.02, market_price=0.99, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size <= 50

def test_filter_opportunity_passes():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=False,
    )
    result = filter_opportunity(
        opp, min_edge=0.12, min_confidence=0.70,
        min_hours=2, max_hours=168,
    )
    assert result is True

def test_filter_opportunity_low_edge():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.60, market_p=0.55, edge=0.05,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=False,
    )
    result = filter_opportunity(opp, min_edge=0.12, min_confidence=0.70, min_hours=2, max_hours=168)
    assert result is False

def test_filter_opportunity_stale_station():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=True,
    )
    result = filter_opportunity(opp, min_edge=0.12, min_confidence=0.70, min_hours=2, max_hours=168)
    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement mismatch.py**

Key components:
- `EdgeResult` dataclass: `raw_edge`, `direction` (YES/NO), `ev`
- `OpportunitySignal` dataclass: all fields needed for filtering
- `compute_edge(our_p, market_p) -> EdgeResult` — corrected EV formula (`ev = edge`)
- `compute_kelly_size(edge, market_price, direction, bankroll, kelly_fraction, fee, max_position, min_position) -> float` — YES-side and NO-side Kelly formulas, fee adjustment, price clamping [0.05, 0.95], raw kelly cap at 0.25
- `filter_opportunity(opp, min_edge, min_confidence, min_hours, max_hours) -> bool` — all threshold checks

- [ ] **Step 4: Run tests, commit**

```bash
rtk git add polymarket_weather/trading/mismatch.py tests/test_mismatch.py
rtk git commit -m "feat: mismatch detector with corrected Kelly sizing + edge formulas"
```

---

## Task 2: Risk Manager

**Files:**
- Create: `polymarket_weather/trading/risk.py`
- Create: `tests/test_risk.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from polymarket_weather.trading.risk import RiskManager, RiskCheck

def test_risk_check_passes():
    rm = RiskManager(
        max_position_usdc=50, max_total_exposure_usdc=600,
        max_open_positions=20, daily_loss_cap_usdc=200,
        max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
        drawdown_pause_pct=0.15, bootstrap_trades=50,
        bootstrap_size_usdc=10, min_trade_size_usdc=5,
    )
    check = rm.check_trade(
        size_usdc=25.0, city="new york", region="northeast_us",
        market_id="0xabc",
    )
    assert check.approved is True

def test_risk_rejects_over_max_position():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    check = rm.check_trade(size_usdc=60.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "max_position" in check.reason

def test_risk_rejects_duplicate_market():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_entry("0xabc", "nyc", "ne", 25.0)
    check = rm.check_trade(size_usdc=25.0, city="nyc", region="ne", market_id="0xabc")
    assert check.approved is False
    assert "duplicate" in check.reason

def test_risk_total_exposure_limit():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=100,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    rm.record_entry("0x2", "la", "sw", 40.0)
    check = rm.check_trade(size_usdc=20.0, city="chi", region="mw", market_id="0x3")
    assert check.approved is False
    assert "total_exposure" in check.reason

def test_risk_city_exposure_limit():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=60, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    check = rm.check_trade(size_usdc=20.0, city="nyc", region="ne", market_id="0x2")
    assert check.approved is False
    assert "city_exposure" in check.reason

def test_risk_region_exposure_limit():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=80,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    rm.record_entry("0x2", "boston", "ne", 20.0)
    check = rm.check_trade(size_usdc=20.0, city="philly", region="ne", market_id="0x3")
    assert check.approved is False
    assert "region_exposure" in check.reason

def test_risk_bootstrap_sizing():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    assert rm.get_max_size() == 10.0  # In bootstrap phase (0 completed trades)
    for i in range(50):
        rm.record_completed_trade()
    assert rm.get_max_size() == 50.0  # Past bootstrap

def test_risk_daily_loss_cap():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=50,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_daily_loss(45.0)
    check = rm.check_trade(size_usdc=10.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "daily_loss" in check.reason

def test_risk_record_exit():
    rm = RiskManager(max_position_usdc=50, max_total_exposure_usdc=600,
                     max_open_positions=20, daily_loss_cap_usdc=200,
                     max_exposure_per_city_usdc=150, max_exposure_per_region_usdc=250,
                     drawdown_pause_pct=0.15, bootstrap_trades=50,
                     bootstrap_size_usdc=10, min_trade_size_usdc=5)
    rm.record_entry("0xabc", "nyc", "ne", 25.0)
    assert rm.total_exposure == 25.0
    rm.record_exit("0xabc")
    assert rm.total_exposure == 0.0
```

- [ ] **Step 2: Implement risk.py**

`RiskCheck` dataclass: `approved: bool`, `reason: str`

`RiskManager` class:
- Tracks: open positions (market_id → {city, region, size}), total exposure, city exposure, region exposure, daily losses, completed trade count
- `check_trade(size_usdc, city, region, market_id) -> RiskCheck` — runs all pre-trade checks
- `record_entry(market_id, city, region, size)` — track new position
- `record_exit(market_id)` — remove position
- `record_daily_loss(amount)` — accumulate daily losses
- `record_completed_trade()` — increment trade count for bootstrap
- `get_max_size() -> float` — returns bootstrap_size if under bootstrap_trades, else max_position
- `reset_daily()` — reset daily loss counter (called by scheduler)

- [ ] **Step 3: Run tests, commit**

---

## Task 3: Trade Executor (adapted)

**Files:**
- Create: `polymarket_weather/trading/executor.py`
- Create: `tests/test_executor.py`

- [ ] **Step 1: Write failing tests**

Test paper trading simulation and order lifecycle:

```python
import pytest
from polymarket_weather.trading.executor import TradeExecutor

@pytest.fixture
def executor():
    return TradeExecutor(paper_trading=True, paper_balance=1000.0,
                         max_slippage=0.02, max_retries=3)

async def test_paper_trade_buy(executor):
    result = await executor.execute_order(
        token_id="tok_yes", side="BUY", amount=25.0,
        price=0.55, order_type="limit",
    )
    assert result.status == "filled"
    assert result.order_id.startswith("paper_")
    assert abs(result.fill_price - 0.55) < 0.01

async def test_paper_trade_sell(executor):
    result = await executor.execute_order(
        token_id="tok_yes", side="SELL", amount=25.0,
        price=0.70, order_type="limit",
    )
    assert result.status == "filled"

async def test_paper_balance_tracking(executor):
    assert executor.get_balance() == 1000.0
    await executor.execute_order("tok", "BUY", 100.0, 0.50, "limit")
    assert executor.get_balance() < 1000.0

def test_slippage_check(executor):
    assert executor.check_slippage(0.55, 0.56) is True   # Within 2%
    assert executor.check_slippage(0.55, 0.60) is False  # Exceeds 2%
```

- [ ] **Step 2: Implement executor.py**

Adapted from `polymarket_bot/execution/engine.py`. Keep:
- Paper trading simulation with random slippage
- `check_slippage()` method
- Retry with exponential backoff pattern
- `OrderResult` dataclass (order_id, fill_price, status, error)

Strip: all arb methods. Add: `get_balance()` for paper mode tracking.

The real CLOB integration (py-clob-client) stays the same pattern but wrapped in simpler methods. The executor does NOT import or depend on the old `polymarket_bot` package.

- [ ] **Step 3: Run tests, commit**

---

## Task 4: Position Manager

**Files:**
- Create: `polymarket_weather/trading/positions.py`
- Create: `tests/test_positions.py`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from datetime import datetime, timezone, timedelta
from polymarket_weather.trading.positions import PositionManager, TrackedPosition

def test_track_entry():
    pm = PositionManager()
    pm.track_entry("0xabc", direction="YES", entry_price=0.55, size_usdc=25.0,
                   city="nyc", event_id="evt_1")
    assert "0xabc" in pm.positions
    assert pm.positions["0xabc"].direction == "YES"

def test_track_exit():
    pm = PositionManager()
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    pm.track_exit("0xabc")
    assert "0xabc" not in pm.positions

def test_compute_pnl_yes_win():
    pos = TrackedPosition(market_id="0x1", direction="YES", entry_price=0.55,
                          size_usdc=25.0, city="nyc", event_id="evt_1",
                          entry_time=datetime.now(timezone.utc))
    pnl = pos.compute_pnl(current_price=0.75)
    assert pnl > 0

def test_compute_pnl_yes_loss():
    pos = TrackedPosition(market_id="0x1", direction="YES", entry_price=0.55,
                          size_usdc=25.0, city="nyc", event_id="evt_1",
                          entry_time=datetime.now(timezone.utc))
    pnl = pos.compute_pnl(current_price=0.40)
    assert pnl < 0

def test_compute_settlement_pnl_yes_wins():
    pos = TrackedPosition(market_id="0x1", direction="YES", entry_price=0.55,
                          size_usdc=25.0, city="nyc", event_id="evt_1",
                          entry_time=datetime.now(timezone.utc))
    pnl = pos.compute_settlement_pnl(outcome="YES", fee=0.01)
    assert pnl > 0  # Bought YES at 0.55, resolved YES → profit

def test_compute_settlement_pnl_yes_loses():
    pos = TrackedPosition(market_id="0x1", direction="YES", entry_price=0.55,
                          size_usdc=25.0, city="nyc", event_id="evt_1",
                          entry_time=datetime.now(timezone.utc))
    pnl = pos.compute_settlement_pnl(outcome="NO", fee=0.01)
    assert pnl < 0  # Bought YES at 0.55, resolved NO → loss

def test_should_exit_edge_inversion():
    pm = PositionManager(edge_inversion_threshold=-0.05)
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    # Current edge is -0.10 (our forecast now says NO)
    should, reason = pm.check_exit("0xabc", current_price=0.55, current_edge=-0.10)
    assert should is True
    assert "edge_inversion" in reason

def test_open_positions_count():
    pm = PositionManager()
    pm.track_entry("0x1", "YES", 0.50, 20.0, "nyc", "e1")
    pm.track_entry("0x2", "NO", 0.60, 30.0, "la", "e2")
    assert pm.open_count == 2
    assert pm.total_exposure == 50.0
```

- [ ] **Step 2: Implement positions.py**

`TrackedPosition` dataclass with `compute_pnl(current_price)` and `compute_settlement_pnl(outcome, fee)`.

`PositionManager` class:
- `positions: dict[str, TrackedPosition]`
- `track_entry(market_id, direction, entry_price, size_usdc, city, event_id)`
- `track_exit(market_id)`
- `check_exit(market_id, current_price, current_edge) -> tuple[bool, str]` — edge inversion check
- Properties: `open_count`, `total_exposure`

- [ ] **Step 3: Run tests, commit**

---

## Task 5: Telegram Alerts (adapted)

**Files:**
- Create: `polymarket_weather/alerts/telegram.py`
- Create: `tests/test_telegram.py`

- [ ] **Step 1: Adapt from existing bot**

Copy `polymarket_bot/notifications/telegram.py` to `polymarket_weather/alerts/telegram.py`. Modify:
- Update imports to use `polymarket_weather` types
- Update `send_trade_notification` to show weather-specific info (city, forecast, edge)
- Add `send_opportunity_alert(city, our_p, market_p, edge, forecast_source)`
- Add `send_stale_station_alert(station_id, last_report_hours_ago)`
- Add `send_settlement_alert(market_id, outcome, pnl)`
- Keep: approval flow, callback handling, daily report

- [ ] **Step 2: Write basic tests**

```python
import pytest
from polymarket_weather.alerts.telegram import WeatherTelegramNotifier

def test_notifier_init():
    n = WeatherTelegramNotifier(bot_token="test", chat_id="123")
    assert n.name == "telegram"

def test_format_opportunity_message():
    from polymarket_weather.alerts.telegram import format_opportunity_message
    msg = format_opportunity_message(
        city="NYC", question="Will NYC high be 50-54F?",
        our_p=0.75, market_p=0.55, edge=0.20, source="metar",
    )
    assert "NYC" in msg
    assert "0.20" in msg or "20" in msg
```

- [ ] **Step 3: Commit**

---

## Task 6: Scheduler (APScheduler 4.x)

**Files:**
- Create: `polymarket_weather/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write tests**

```python
import pytest
from polymarket_weather.scheduler import build_schedules, parse_cron_time

def test_parse_cron_time():
    hour, minute = parse_cron_time("08:00")
    assert hour == 8
    assert minute == 0

def test_parse_cron_time_afternoon():
    hour, minute = parse_cron_time("14:30")
    assert hour == 14
    assert minute == 30

def test_build_schedules_returns_all_jobs():
    schedules = build_schedules(
        metar_poll=1800, taf_poll=21600, nwp_poll=21600,
        market_scan=300, mismatch_detection=300,
        trade_execution=60, position_monitor=120,
        settlement_check=600, stale_data_check=900,
        daily_report="08:00", calibration_update="06:00",
    )
    job_names = {s["id"] for s in schedules}
    assert "metar_poll" in job_names
    assert "market_scan_and_mismatch" in job_names  # Combined job
    assert "trade_execution" in job_names
    assert "daily_report" in job_names
```

- [ ] **Step 2: Implement scheduler.py**

Uses APScheduler 4.x `AsyncScheduler`:
- `parse_cron_time(time_str) -> tuple[int, int]`
- `build_schedules(...)` — returns schedule configs (not the scheduler itself, for testability)
- `create_scheduler(schedules, job_functions) -> AsyncScheduler` — creates and configures the scheduler
- Combined `market_scan_and_mismatch` job (scan then detect, sequential)
- `IntervalTrigger` for polling jobs, `CronTrigger` for daily jobs
- `CoalescePolicy.latest` on all jobs

- [ ] **Step 3: Commit**

---

## Task 7: App Entrypoint

**Files:**
- Create: `polymarket_weather/app.py`

- [ ] **Step 1: Implement app.py**

The main `run_bot()` async function that wires everything together:

1. Load config, call `load_dotenv()`
2. Init DB session factory
3. Create all components: MetarCollector, NwpFetcher, WeatherMarketScanner, CityMapper, ForecastEngine, MismatchDetector, RiskManager, TradeExecutor, PositionManager
4. Create EventBus, wire handlers
5. Create Telegram notifier (if enabled)
6. Build APScheduler with all job functions
7. Start scheduler, run until stopped
8. Graceful shutdown

Each scheduled job is a thin async function that calls the appropriate module methods. The job functions close over the component instances.

- [ ] **Step 2: Basic smoke test**

```python
def test_app_module_imports():
    """Verify app.py can be imported without errors."""
    from polymarket_weather import app
    assert hasattr(app, "run_bot")
```

- [ ] **Step 3: Commit**

---

## Task 8: CLI Dashboard

**Files:**
- Create: `polymarket_weather/cli.py`

- [ ] **Step 1: Implement cli.py**

Adapted from `polymarket_bot/cli.py`. Keep:
- Rich formatting utilities: `format_price`, `format_pnl`, `format_pct`, `_time_ago`
- Status bar panel (bankroll, PnL, exposure, paper mode)
- Positions table
- Recent trades panel

Replace:
- Signals panel → Weather forecasts panel (station, temp, forecast, edge)
- Plugin status → Station health (last update, staleness)

New:
- `print_banner()` with weather bot branding
- `build_weather_dashboard(...)` assembling all panels

- [ ] **Step 2: Commit**

---

## Verification Checklist

After all tasks complete:
- [ ] `python -m pytest tests/ -v` — all tests pass (Plan 1 + Plan 2)
- [ ] Config loads and all components initialize
- [ ] Paper trading mode simulates orders correctly
- [ ] Risk manager enforces all limits
- [ ] Kelly sizing uses corrected formulas (YES + NO side, fee adjustment)
- [ ] Position tracking computes PnL correctly
- [ ] Scheduler defines all 12 jobs from the spec
- [ ] `python -m polymarket_weather` starts the bot (paper mode)
