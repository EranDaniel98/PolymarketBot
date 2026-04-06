# PolymarketWeatherBot — Operations Runbook

Practical guide for deploying, monitoring, and operating the bot. Assumes
familiarity with the architecture (see `docs/superpowers/specs/`).

---

## Table of contents

1. [Architecture at a glance](#architecture-at-a-glance)
2. [Daily monitoring](#daily-monitoring)
3. [Deploying a change](#deploying-a-change)
4. [Rotating secrets](#rotating-secrets)
5. [Database operations](#database-operations)
6. [Pausing and resuming trading](#pausing-and-resuming-trading)
7. [Switching from paper to live trading](#switching-from-paper-to-live-trading)
8. [Reading logs](#reading-logs)
9. [Common incidents](#common-incidents)
10. [Disaster recovery](#disaster-recovery)

---

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│  Railway service: PolymarketWeatherBot                          │
│                                                                 │
│  ┌──────────────────────────────────┐  ┌──────────────────┐    │
│  │  asyncio.TaskGroup (server.py)   │  │  PostgreSQL      │◄───┤
│  │                                   │  │  (Railway plugin)│    │
│  │  ├─ uvicorn (FastAPI dashboard)  │  └──────────────────┘    │
│  │  ├─ run_bot (app.py)             │                           │
│  │  │   ├─ metar_poll (30 min)      │  ┌──────────────────┐    │
│  │  │   ├─ market_scan (5 min)      │──│  Polymarket Gamma│    │
│  │  │   │   └─ MismatchPipeline     │  └──────────────────┘    │
│  │  │   └─ stale_data_check (15 m)  │                           │
│  │  └─ signal_watcher (SIGTERM)     │  ┌──────────────────┐    │
│  └──────────────────────────────────┘──│  aviationweather │    │
│                                         │  open-meteo      │    │
│                                         │  Telegram        │    │
│                                         └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
        ▲
        │ HTTPS, X-API-Key auth (DASH_PASS)
        │
   React dashboard (frontend/dist served by FastAPI at /)
```

**Single process, single container.** No worker pool, no message queue. The
asyncio scheduler runs three jobs at fixed intervals; FastAPI serves the
dashboard from the same event loop.

**State of record:** PostgreSQL. Both PositionManager and RiskManager
reconcile from the DB on startup, so process crashes are non-destructive.

---

## Daily monitoring

### Check the bot is alive

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  https://polymarketweatherbot-production-12a6.up.railway.app/api/health
```

Expected: `200`.

### Check the metrics endpoint

```bash
curl -s -H "X-API-Key: $DASH_PASS" \
  https://polymarketweatherbot-production-12a6.up.railway.app/api/metrics
```

What to look for:

| Metric | Healthy | Investigate if |
|---|---|---|
| `pmw_paper_mode` | `1` (paper) or `0` (live) | mismatches your intent |
| `pmw_breaker_state{name="metar"}` | `0` (closed) | `2` for >10 min |
| `pmw_breaker_state{name="polymarket_gamma"}` | `0` | `2` for >10 min |
| `pmw_breaker_state{name="nwp_ensemble"}` | `0` | `2` for >10 min |
| `pmw_open_positions` | matches dashboard | drifts from dashboard |
| `pmw_total_exposure_usdc` | within `max_total_exposure` | within ~10% of cap |
| `pmw_daily_loss_usdc` | < `daily_loss_cap_usdc` | within ~25% of cap |

A breaker stuck open means the upstream is failing — check Railway logs for
the actual error. Each breaker auto-half-opens after its reset timeout
(120s for metar/gamma, 300s for nwp).

### Check the dashboard

Visit https://polymarketweatherbot-production-12a6.up.railway.app/ in a
browser and authenticate. The Overview, Opportunities, Positions, History,
and Weather pages all hit `/api/*` routes that require the `X-API-Key`
header.

---

## Deploying a change

```bash
# 1. Make changes locally, run tests
python -m pytest tests/ --deselect tests/test_weather_config.py::test_load_config_from_yaml

# 2. Commit and push
git add <files>
git commit -m "feat: ..."
git push

# 3. Deploy to Railway
railway up --ci

# 4. Verify the new deploy
curl -s -o /dev/null -w "health:%{http_code}\n" \
  https://polymarketweatherbot-production-12a6.up.railway.app/api/health
railway logs 2>&1 | tail -30
```

The deployment is hands-off — Railway pulls the latest image, runs the
multi-stage Docker build (Node frontend + Python backend), and rolls over.
Health checks at `/api/health` gate the new container's traffic.

If the new container crash-loops, Railway will keep the old one running.
You'll see this in `railway logs` as repeated startup banner + crash.

---

## Rotating secrets

The bot uses 8 environment variables. All except `DATABASE_URL` need rotation
when something suspicious happens (e.g. private key potentially leaked).

| Variable | How to rotate |
|---|---|
| `POLYMARKET_PRIVATE_KEY` | Generate new key, export to .env, `railway variables --set` |
| `POLYMARKET_API_KEY` | Re-derive via `python scripts/generate_clob_creds.py` |
| `POLYMARKET_API_SECRET` | Same — derived from private key |
| `POLYMARKET_API_PASSPHRASE` | Same — derived from private key |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/mybots` → API Token → Revoke |
| `TELEGRAM_CHAT_ID` | Not a secret, no rotation needed |
| `DASH_PASS` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DATABASE_URL` | Auto-injected from Railway Postgres plugin |

After rotating:

```bash
# Update Railway
railway variables --set "POLYMARKET_PRIVATE_KEY=0x..."
# Update local .env
# Redeploy so the new value is loaded
railway up --ci
```

**Verify the new private key derives the right address:**

```bash
railway run python -c "
import os
from eth_account import Account
print(Account.from_key(os.environ['POLYMARKET_PRIVATE_KEY']).address)
"
```

---

## Database operations

### Connect via Railway

```bash
railway connect postgres
```

This drops you into a `psql` session with the production DB.

### Useful queries

```sql
-- Open positions
SELECT id, market_id, direction, city, size_usdc, fill_price, placed_at
FROM trades WHERE status = 'open';

-- Today's P&L
SELECT SUM(pnl_usdc) FROM trades
WHERE status = 'closed' AND DATE(settled_at) = CURRENT_DATE;

-- METAR freshness
SELECT station_id, last_report_at,
       NOW() - last_report_at AS age
FROM icao_stations ORDER BY last_report_at;

-- Recent system events
SELECT created_at, severity, event_type, message
FROM system_events ORDER BY created_at DESC LIMIT 50;

-- Daily loss counter (Phase 2.2 SystemState)
SELECT * FROM system_state WHERE key LIKE 'daily_loss:%' ORDER BY key DESC LIMIT 7;
```

### Schema migrations

Currently using an `ensure_schema()` helper in
`polymarket_weather/db/persistence.py` that runs `ALTER TABLE ADD COLUMN
IF NOT EXISTS` on every boot. This is a bridge until Phase 7.4 lands
proper Alembic migrations.

When adding a new column to a model:

1. Update `polymarket_weather/db/models.py`
2. Add the column name + type to `_TRADE_COLUMN_ADDS` in `persistence.py`
   (or create a new ALTER block for other tables)
3. Deploy — `ensure_schema()` runs at startup and applies the ALTER

For destructive changes (DROP, type change), do it manually via
`railway connect postgres` then update the model.

---

## Pausing and resuming trading

The bot has a soft kill-switch via the `system_state` table:

```sql
INSERT INTO system_state (key, value, updated_at)
VALUES ('is_paused', 'true', NOW())
ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = NOW();
```

Set `is_paused = false` to resume. The risk manager checks this on every
trade attempt (Phase 2.3 wired this through).

**For an immediate hard stop**, use Railway's "Stop" button in the dashboard
or:

```bash
railway down
```

This terminates the container. State is preserved in the DB; restart with
`railway up --ci`.

---

## Switching from paper to live trading

**Two gates must both be cleared before any real money flows:**

1. `trading.paper_trading: false` in `config.railway.yaml`
2. `LIVE_TRADING_CONFIRMED=yes` env var on Railway

The bot's executor checks BOTH at startup. Either gate alone will not
enable live trading. This is intentional belt-and-suspenders against an
accidental config flip.

**Pre-flight checklist before flipping:**

- [ ] New self-custody wallet funded with USDC + MATIC (gas) on Polygon
- [ ] CLOB API credentials regenerated for the new wallet (`scripts/generate_clob_creds.py`)
- [ ] All tests passing locally (`python -m pytest tests/`)
- [ ] Phase 0 security review completed
- [ ] At least 7 days of paper-trading history with healthy P&L
- [ ] Calibration data shows our_p tracking observed_rate within 5%
- [ ] Risk limits set conservatively (`max_position_usdc <= 25`,
      `daily_loss_cap_usdc <= 100`)
- [ ] Telegram alerts confirmed working
- [ ] Kill-switch drill: `is_paused=true` actually stops trades

Then:

```bash
# 1. Update config
sed -i 's/paper_trading: true/paper_trading: false/' config.railway.yaml

# 2. Set the env gate
railway variables --set "LIVE_TRADING_CONFIRMED=yes"

# 3. Commit + deploy
git add config.railway.yaml
git commit -m "ops: enable live trading"
git push
railway up --ci

# 4. WATCH THE LOGS for the next 30 minutes
railway logs --follow
```

**Reverting to paper:**

```bash
sed -i 's/paper_trading: false/paper_trading: true/' config.railway.yaml
railway variables --set "LIVE_TRADING_CONFIRMED=" # blank, not unset
git add config.railway.yaml
git commit -m "ops: revert to paper"
railway up --ci
```

---

## Reading logs

The bot emits structured JSON logs (when `LOG_FORMAT=json`, set on Railway):

```
[INFO] METAR poll: 76 new readings ts="2026-04-06T10:00:12" logger="polymarket_weather"
[ERRO] breaker 'metar' OPENED after 5 consecutive failures ts="..." logger="polymarket_weather.resilience"
```

Filter Railway logs by component:

```bash
# All scheduler activity
railway logs 2>&1 | grep "polymarket_weather.runtime"

# All trading pipeline activity
railway logs 2>&1 | grep "polymarket_weather.trading"

# Errors only
railway logs 2>&1 | grep "ERRO"

# Skip telegram noise
railway logs 2>&1 | grep -v "telegram"
```

**Secret redaction is automatic.** Private keys, Telegram tokens, and DB
URLs with credentials are scrubbed before they reach the log handlers
(see `polymarket_weather/logging_filters.py`).

---

## Common incidents

### "All METAR stations stale" alarm fires

1. Check the breaker: `curl /api/metrics | grep metar`
2. If `pmw_breaker_state{name="metar"} 2`, the breaker is open. Wait
   120s for half-open probe, or check aviationweather.gov status manually.
3. If breaker is closed but stations are stale, check `metar_poll` job
   logs: `railway logs | grep "METAR poll"`. New readings should appear
   every 30 min.
4. If `metar_poll` is silent, the scheduler may be wedged. Restart:
   `railway redeploy`.

### Dashboard returns 401 with the right key

The most likely cause is a key mismatch between Railway and what you're
sending. Verify:

```bash
railway variables 2>&1 | grep DASH_PASS
echo "$DASH_PASS"
```

If they differ, update one to match the other. Make sure there's no
trailing newline or whitespace in the env var.

### Telegram bot is silent

1. Check the token is set: `railway variables 2>&1 | grep TELEGRAM`
2. Check Telegram-specific logs: `railway logs | grep telegram`
3. If you see `Conflict: terminated by other getUpdates request`, you have
   two bot instances polling. This usually self-resolves after a deploy
   handover. If persistent, rotate the bot token via @BotFather.
4. If you see `InvalidToken`, the token in Railway doesn't match what
   @BotFather thinks. Re-revoke and re-set.

### Bot deployed but no opportunities ever appear

1. Check market scan output: `railway logs | grep "Market scan"`. If
   reporting `0 weather markets`, **Polymarket has no active weather
   markets**. They're seasonal. This is correct behavior, not a bug.
2. Verify directly: `railway run python -c "import httpx; print(len(httpx.get('https://gamma-api.polymarket.com/events?active=true&closed=false&limit=500', timeout=30).json()))"`
3. If markets exist but the pipeline rejects them all, check the aggregate
   logs: `railway logs | grep "Pipeline:"`. The skipped reasons are listed.

### Database connection errors

1. Check the Postgres plugin is healthy in the Railway dashboard.
2. Try a fresh connect: `railway connect postgres`
3. If asyncpg can't connect but psql can, check that
   `config.py:_apply_env_overrides` is rewriting `postgresql://` →
   `postgresql+asyncpg://`.

---

## Disaster recovery

### Bot crashed mid-trade

**State on disk:** PostgreSQL has every open position via the `trades`
table with `status='open'`, populated by `persist_position_entry()`.

**On restart:** `app.py` calls `persistence.load_open_positions()` which
rebuilds `PositionManager._positions` and `RiskManager._positions` from
the DB. No state loss.

**Verify after restart:**

```sql
-- Should see N rows where N = positions before crash
SELECT COUNT(*) FROM trades WHERE status = 'open';
```

```bash
curl -s -H "X-API-Key: $DASH_PASS" \
  https://.../api/positions | jq length
```

The two should match.

### Lost the wallet private key

**This is unrecoverable for the wallet itself.** Funds in the wallet are
gone unless you have a seed phrase backup.

**For the bot:**
1. Generate a new wallet (`scripts/generate_clob_creds.py` after running
   the `eth_account.Account.create()` snippet)
2. Re-derive CLOB credentials
3. Update Railway env vars with new values
4. Redeploy
5. The bot will start trading from a fresh wallet — open positions in
   the OLD wallet are now orphaned.

### Lost the entire Railway project

1. Re-create the project: `railway init --name PolymarketWeatherBot`
2. Add Postgres: `railway add --database postgres --json`
3. Set all 7 secrets from your password manager via `railway variables --set`
4. `railway up --ci`
5. The new database is empty. If you have a Postgres backup, restore it
   via `pg_restore` against the new DB connection.

---

## Quick reference

```bash
# Project URL (auth required for everything except /api/health)
https://polymarketweatherbot-production-12a6.up.railway.app

# CLI essentials
railway logs                  # tail logs
railway logs --follow         # follow logs
railway variables             # list env vars
railway run <cmd>             # run a command in the Railway env
railway connect postgres      # psql session
railway up --ci               # deploy
railway down                  # stop the service

# Local
python -m pytest tests/ --deselect tests/test_weather_config.py::test_load_config_from_yaml
python -m ruff check polymarket_weather/ tests/
python -m polymarket_weather  # run the bot locally (uses .env + config.yaml)
```

---

*Last updated: Phase 7 of the hardening plan.*
