# Hardening Plan: Polymarket Weather Bot → 9+/10 Across All Dimensions

**Created:** 2026-04-06
**Branch:** `weather-rewrite`
**Current scores:** Arch 6.5 / Math 7.5 / Code Quality 7.5 / Tests 7.0 / Security 4.0
**Target scores:** all ≥ 9.0
**Basis:** synthesis of 5 parallel audit agents (architecture, bugs, trading math, security, quality)

---

## Guiding principles

- **No regressions.** Each phase ends with a green `pytest` run and a successful local boot.
- **One concern per commit.** Small, reviewable commits, pushed to `weather-rewrite`.
- **Verification before claiming done.** Every task lists an explicit verification command + expected output.
- **Secrets never in logs or errors.** Treat the private key as hot lava.
- **Math changes get a test first** (TDD) — the bot decides real money.

---

## Phase 0 — Emergency security (same-day, before anything else)

The bot is live on a public Railway URL with no auth and real secrets in `config.yaml`. These must be fixed first.

### 0.1 Redact DB URL from logs + switch off SQLAlchemy echo
- **Files:** `polymarket_weather/db/session.py`, `polymarket_weather/app.py`
- **Change:** add a logging filter that masks `://user:pass@` in any log record; confirm `create_async_engine(echo=False)` (default but assert it).
- **Verify:** `pytest tests/test_db_models.py && grep -r "echo=True" polymarket_weather/` returns nothing.

### 0.2 Wire `DASH_PASS` → HTTPBasic auth on mutation endpoints
- **File:** `polymarket_weather/api/dashboard.py`
- **Change:** add `HTTPBasic` dependency; require it on every `PUT`/`POST`/`DELETE` endpoint. `GET /api/health` stays public (Railway health check). Decide: protect read-only `GET`s too? **Yes** — positions/opportunities leak strategy. Make all non-health routes auth-required.
- **New env behavior:** if `DASH_PASS` empty, boot fails fast with a clear error (no accidental open dashboard).
- **Verify:** `curl /api/overview` → 401; `curl -u admin:$DASH_PASS /api/overview` → 200.

### 0.3 Input-validate `/api/config` PUT
- **File:** `polymarket_weather/api/dashboard.py`
- **Change:** whitelist of allowed keys + per-key type coercion + bounds check (e.g. `max_position_usdc ∈ [1, 1000]`). Reject everything else with 400.

### 0.4 Add rate limiting
- **File:** `polymarket_weather/api/dashboard.py`
- **Dep:** `slowapi>=0.1.9`
- **Limits:** 60/min read, 5/min write, 120/min health.

### 0.5 Dockerfile runs as non-root
- **File:** `Dockerfile`
- **Change:** create `appuser` (uid 1000), `USER appuser` before `CMD`. Ensure `/app` readable by uid 1000.

### 0.6 Rotate secrets + scrub `config.yaml`
- **User action:** rotate Polygon private key (move funds to a new wallet, create fresh CLOB API creds), rotate Telegram bot token, rotate any LLM keys.
- **Local:** replace `config.yaml` with an all-empty-secrets version matching `config.example.yaml`; put real values in a `.env` file (already gitignored).
- **Railway:** already has env vars set; just update them to the new values.
- **Add to `.dockerignore`:** explicit `config.yaml`, `.env`, `.env.*`.

### 0.7 Set `DASH_PASS` on Railway
- `railway variables --set DASH_PASS=<strong-pw>`
- Redeploy.

**Phase 0 exit criteria:** all secrets rotated; public Railway URL returns 401 on every non-health route; local tests pass; Docker runs as uid 1000.

---

## Phase 1 — Critical correctness bugs

These are real bugs the audit found. Each gets a failing test first, then the fix.

### 1.1 Wire the scheduler (THE bug — bot is currently idle)
- **File:** `polymarket_weather/app.py`
- **Symptom:** job coroutines defined (`metar_poll_job`, `market_scan_and_mismatch_job`, `stale_data_check_job`) but never invoked; main loop is `while True: await asyncio.sleep(60)`.
- **Design choice:** Don't introduce APScheduler 4.x (alpha-only); use a lightweight `asyncio.TaskGroup` with `_interval_runner(coro, seconds)` helpers. This keeps the process single-threaded, cancellable, and testable.
- **Implementation sketch:**
  ```python
  async def _interval_runner(name, coro_factory, seconds):
      while True:
          try:
              await coro_factory()
          except asyncio.CancelledError:
              raise
          except Exception:
              logger.exception("job %s failed", name)
          await asyncio.sleep(seconds)

  async with asyncio.TaskGroup() as tg:
      tg.create_task(_interval_runner("metar", metar_poll_job, config.scheduler.metar_poll))
      tg.create_task(_interval_runner("scan",  market_scan_and_mismatch_job, config.scheduler.market_scan))
      tg.create_task(_interval_runner("stale", stale_data_check_job, config.scheduler.stale_data_check))
      tg.create_task(_interval_runner("positions", position_monitor_job, config.scheduler.position_monitor))
      tg.create_task(_interval_runner("settle",   settlement_check_job, config.scheduler.settlement_check))
  ```
- **Complete `# TODO: run mismatch detection on scanned markets`** in `market_scan_and_mismatch_job`: for each scanned market, fetch forecast for its city+horizon, run `MismatchDetector.evaluate`, push result onto `EventBus`, persist to `opportunities` table.
- **Test:** `tests/test_app_scheduler.py` — monkeypatch job functions, start `run_bot` in a task, assert each job is called within 2s, then cancel cleanly.
- **Verify:** deploy, watch `railway logs` — METAR poll messages should appear every 30 min; opportunities should start populating the DB.

### 1.2 Fix `_detect_unit` Celsius miss (`markets/parser.py:80–88`)
- Replace the nested-if with a single condition. Add tests for: `"above 35 °C"`, `"35 degrees C"`, `"35°C"`, `"35 celsius"`, `"above 35°F"` (negative control).
- **Severity:** high — wrong unit → wrong threshold → wrong probability.

### 1.3 Fix `datetime.now(target.tzinfo or None)` (`weather/forecast.py:214`)
- Replace with `datetime.now(timezone.utc)`. Add test that passes a naive `target` and asserts no TypeError.

### 1.4 Guard `np.std(..., ddof=1)` against single-member ensembles (`weather/nwp.py:44`)
- If `len(values) < 2`: return `None` (not 0.0 — caller must treat as "no signal"). Propagate the `None` up into `ForecastEngine`, which should then fall back to RMSE table.
- Add test for 0, 1, 2 member cases.

### 1.5 Fail-fast CLOB init (`trading/executor.py:44–54`)
- If `paper_trading is False` and CLOB client creation raises, `raise` instead of log-and-continue. Bot must NEVER run live with a broken executor.
- Paper mode keeps current lenient behavior.

### 1.6 Validate probability inputs
- `compute_probability_range(lower, upper)`: raise `ValueError` if `lower > upper`.
- `compute_from_*`: raise `ValueError` if `sigma < 0`.
- Add tests.

### 1.7 Fix stale-station false negative (`weather/collector.py:179–184`)
- Track which `station_id`s actually produced new rows; only update `IcaoStation.last_report_at` for those.
- Add test with a mixed duplicate/new batch.

**Phase 1 exit criteria:** all 7 tests above are green; Railway deploy shows METAR/scan jobs running; opportunities table starts filling.

---

## Phase 2 — State persistence & crash recovery

### 2.1 Persist positions to DB
- **New table (or reuse `trades`):** `positions` with columns `id, market_token_id, side, size_shares, entry_price, entry_ts, status, exit_price, exit_ts, realized_pnl`.
- **`PositionManager`**: on entry, `INSERT`; on exit, `UPDATE status='closed'`.
- **`RiskManager`**: stop holding its own dict; read aggregate exposure from DB via a read-through cache refreshed every 10s.
- **Startup reconciliation** in `app.py`: load all `status='open'` positions from DB → reconstruct in-memory views → resume monitoring.
- **Tests:** kill-restart simulation — spawn manager, insert 2 positions, drop manager, re-create, assert state matches.

### 2.2 Daily-loss reset with timestamp
- Add `daily_loss_reset_at` column in `risk_config` or a `system_state` table. On every `check_trade`, if `now().date() > reset_date`, reset counter + update column.

### 2.3 Bootstrap trade count sourced from DB
- Currently in-memory. Query `SELECT COUNT(*) FROM trades WHERE status='settled'` on startup + after each fill.

### 2.4 Single source of truth for positions
- `RiskManager.check_trade` and `PositionManager.on_fill` must not race. Add an `asyncio.Lock` wrapping the "check + execute + record" critical section in the mismatch→executor pipeline.

**Phase 2 exit criteria:** kill-and-restart test shows zero position amnesia; `RiskManager` has no in-memory position dict.

---

## Phase 3 — Architecture & resilience

### 3.1 Structured concurrency (`server.py`)
- Replace `asyncio.wait({bot, web}, FIRST_EXCEPTION)` with `asyncio.TaskGroup`. Clean SIGTERM handling via a top-level `signal.SIGTERM` handler that cancels the group.

### 3.2 Circuit breakers on external APIs
- **Dep:** `pybreaker>=1.0`
- Wrap `httpx` calls to aviationweather.gov, open-meteo, gamma-api.polymarket, Polymarket CLOB with separate breakers. Breaker open → fall back to cached data for reads, skip cycle for writes.
- Expose breaker state in `/api/health` details (auth required).

### 3.3 Structured logging with `structlog`
- Replace `logging.getLogger` usage with `structlog.get_logger()` producing JSON lines on Railway. Add fields: `component`, `market_token_id`, `city`, `horizon_hours`, `edge`.
- Redaction processor strips `private_key`, `api_secret`, `bot_token`, DB URLs.

### 3.4 Metrics endpoint
- `/api/metrics` (auth-required, Prometheus text format): bot heartbeat, jobs-run counter, METAR freshness per station, opportunity count, trade fill rate, current bankroll, current exposure.

### 3.5 Fix `EventBus` silent swallow
- `publish` should collect handler exceptions and log each with `exc_info`; optionally re-raise as `ExceptionGroup`.

### 3.6 Dependency injection of HTTP clients
- `MetarCollector`, `NwpFetcher`, `WeatherMarketScanner`, `TradeExecutor` all create their own `httpx.AsyncClient`. Change constructors to accept an injected client (default `None` → create). Enables mocking and shared connection pooling.

### 3.7 Graceful shutdown with fill drain
- On SIGTERM: stop scheduling new cycles, wait up to 10s for in-flight orders to settle, then cancel.

**Phase 3 exit criteria:** `railway logs` is structured JSON; `/api/metrics` returns Prometheus text; kill -TERM gives clean shutdown under 15s.

---

## Phase 4 — Trading math & model quality

### 4.1 Ensemble sigma: blend inter-member spread with RMSE table
- Current: uses raw ensemble std when ≥10 members.
- New: `sigma² = max(ensemble_std², rmse_horizon²) + bias²` — guards against overconfident ensembles and missing systematic error.
- Add `forecast.bias_by_station_month` optional table (populated by calibration job) — default 0.

### 4.2 Fee math: reduce effective edge instead of the ad-hoc `(1 - fee/edge)`
- Replace with: `effective_edge = raw_edge - fee; if effective_edge ≤ 0: skip; raw_kelly = effective_edge / (1 - p)` (YES) or `effective_edge / p` (NO).
- Rewrite `test_mismatch.py::test_kelly_with_fee` with a worked analytic example.

### 4.3 Kelly boundary tests
- Parametrize tests for price ∈ {0.05, 0.10, 0.50, 0.90, 0.95} × edge ∈ {0.01, 0.05, 0.15, 0.30} × direction ∈ {YES, NO}. Verify monotonicity (more edge → more size, more extreme price → more size on favorable side).

### 4.4 Both-side opportunity detection
- When the market has a bid-ask spread wide enough that **both** YES above its bid and NO above its bid clear `min_edge`, the current code picks one. Add explicit test + logic for "take the better EV side" and never both.

### 4.5 Zero-liquidity guard
- In `MismatchDetector.evaluate`: reject markets where `best_bid == 0` or `best_ask == 1` or spread > `max_spread` (new config). Test.

### 4.6 Calibration job actually runs
- `build_schedules()` lists `calibration_update` — wire it into the `_interval_runner` set in Phase 1.1. Output goes to `edge_calibration` table already defined in `db/models.py`.

**Phase 4 exit criteria:** new parametric tests green; calibration table populates after a simulated settled-trade batch.

---

## Phase 5 — Parser refactor

### 5.1 Replace `parse_market_question` linear regex chain with dispatch table
- **File:** `polymarket_weather/markets/parser.py`
- Extract each pattern to a named function `parse_above_threshold`, `parse_range`, `parse_named_threshold`, etc. Each returns `Optional[ParsedMarket]`. `parse_market_question` becomes: `for fn in _PARSERS: r = fn(text); if r: return r`.
- Consolidate `_detect_unit` + `_unit_from_match` into one.
- Goal: reduce the 300-line function to < 40, each helper < 30 lines.
- **Verify:** existing parser tests still pass unchanged (behavior preserved).

### 5.2 Custom exceptions
- Add `polymarket_weather/errors.py` with `InsufficientEdgeError`, `RiskLimitError`, `StaleDataError`, `InvalidMarketError`. Use them instead of bare `ValueError`/returning `None` in trading path.

---

## Phase 6 — Test coverage gaps

Target: every gap the test-coverage audit listed.

### 6.1 Forecast regime transitions
- Parametric test at `hours ∈ {5.99, 6.0, 6.01, 12, 18, 24, 29.99, 30.0, 30.01}`. Assert weight monotonically decreases from 0.6 to 0.3 across the ramp.

### 6.2 METAR parser edge cases
- `-40°C`, `" - 5 degrees"`, `"below zero"`, unusual whitespace, missing T-group fallback.

### 6.3 CLOB executor retry
- Test that transient `ClobApiError` triggers up to `max_retries` with expected backoff (mock `asyncio.sleep` + `random.uniform`).

### 6.4 Position manager peak-PnL amnesia
- Scenario: +50% → +10% → -5%. Assert peak is tracked and trailing-stop references peak, not current.

### 6.5 Concurrent risk updates
- `asyncio.gather` 20 simultaneous `check_trade` calls. Assert exposure never exceeds the limit (tests the lock from 2.4).

### 6.6 Integration smoke test
- End-to-end with httpx and DB mocked at the transport layer: seed 3 fake METAR stations + 2 fake Polymarket markets → run one cycle of each job → assert a paper-trade is recorded in DB.

### 6.7 Dashboard auth tests
- 401 without creds, 200 with creds, 401 with wrong creds, 429 after rate limit.

**Phase 6 exit criteria:** coverage ≥ 85% on `polymarket_weather/{trading,weather,markets}/`; all new tests green.

---

## Phase 7 — Code quality polish

### 7.1 Type hint gaps — run `mypy --strict polymarket_weather/` and fix errors.
### 7.2 Ruff + isort pass — `ruff check --fix polymarket_weather/ tests/`.
### 7.3 Delete `polymarket_bot/` (legacy) — after confirming nothing in `polymarket_weather/` imports it.
### 7.4 Alembic migrations — generate initial migration from current models; remove the `Base.metadata.create_all` shortcut added during Railway bring-up.
### 7.5 Docstring sweep on public APIs (trading/* and weather/*).
### 7.6 Document runbook in `docs/RUNBOOK.md` — how to: deploy, rotate secrets, pause trading, resume, read logs, flip paper→live.

---

## Phase 8 — Observability & dashboard polish

### 8.1 Surface scheduler health in dashboard
- New `/api/jobs` endpoint: last-run time and last-error per job. React page shows red/green per job.

### 8.2 Freshness indicators
- Each METAR station shows time-since-last-report with color coding.

### 8.3 Calibration page works
- Currently shows empty; after Phase 4.6 + settled trades, Recharts reliability diagram should populate. Add a "No data yet — need N more settled trades" placeholder.

### 8.4 Kill switch
- Big red `POST /api/kill_switch` (auth'd) that flips `paper_trading=true` in the live config and broadcasts a SIGHUP so components re-read. Telegram alert on activation.

---

## Execution order & checkpoints

| Phase | Description | Gating check before next phase |
|---|---|---|
| 0 | Security emergency | Public URL 401s; secrets rotated |
| 1 | Critical bugs | Scheduler running; jobs visible in logs; all 7 bug tests green |
| 2 | State persistence | Kill-restart test passes; `RiskManager` DB-backed |
| 3 | Architecture & resilience | JSON logs; `/api/metrics` serves; clean SIGTERM |
| 4 | Trading math | New parametric Kelly tests green; calibration populating |
| 5 | Parser refactor | Existing parser tests unchanged; function line count target met |
| 6 | Test coverage gaps | ≥85% on core modules |
| 7 | Code quality polish | `mypy --strict` + `ruff` clean; legacy removed; Alembic migrations in place |
| 8 | Observability & dashboard polish | Jobs page + kill switch live |

**Recommended commit pacing:** one commit per sub-task (roughly 60 commits total). Push after each phase; deploy to Railway after phases 0, 1, 2, 3, 4, 8.

---

## Success metrics (target scores)

| Dimension | Current | Target | How we get there |
|---|---|---|---|
| **Security** | 4 | 9.5 | Phase 0 + 3.3 (log redaction) + 8.4 (kill switch) |
| **Architecture** | 6.5 | 9.0 | Phase 1.1 (scheduler), 2 (state), 3 (resilience) |
| **Trading math** | 7.5 | 9.5 | Phase 1.3/1.4/1.6 (bugs) + Phase 4 (model) |
| **Code quality** | 7.5 | 9.0 | Phase 5 (parser) + 7 (mypy/ruff/alembic) |
| **Tests** | 7.0 | 9.0 | Phase 6 (gap closure) + TDD discipline through Phase 1–4 |

---

## Out of scope (explicitly deferred)

- Multi-exchange arbitrage (Kalshi, Manifold) — the whole point of the rewrite was weather-only focus.
- Multi-asset (anything beyond temperature) — spec item, not this plan.
- Moving off Railway. Railway is fine for now.
- Full RBAC / user accounts on the dashboard. Single shared `DASH_PASS` is sufficient for a solo-operator bot.
- Replacing `scipy.stats.t` with a custom CDF. `scipy` is already a dependency for other things.

---

## Decisions (locked in 2026-04-06)

1. **Auth scheme:** Bearer token via `X-API-Key: <DASH_PASS>` header. Constant-time comparison. 401 on missing/wrong.
2. **Auth coverage:** ALL routes require auth except `GET /api/health` (Railway probe).
3. **Live-trading enablement:** requires BOTH `LIVE_TRADING_CONFIRMED=yes` env var AND `trading.paper_trading: false` in config. Startup check fails loudly if only one is set.
4. **Secret rotation:** walk-through with user (in progress).
