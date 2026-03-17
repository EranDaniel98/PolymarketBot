# Polymarket Trading Bot — Design Spec

## Overview

A Python-based trading bot for Polymarket that combines **signal-based directional trading** with **cross-platform arbitrage detection**. It runs locally with Telegram/Discord notifications, supports configurable risk management, and trades across all market categories.

## Architecture: Modular Event-Driven

Seven core modules communicate through an in-process async event bus:

```
Signal Plugins ──→ Event Bus ──→ Decision Engine ──→ Execution Engine
  (news, social,       ↑              ↓                    ↓
   polls, LLM,    Arbitrage       Risk Manager         Notifier
   bookmakers)     Engine                            (Telegram/Discord)
```

### Event Bus

- Lightweight in-process pub/sub using Python `asyncio` queues
- Typed event dataclasses: `SignalEvent`, `ArbitrageOpportunity`, `TradeDecision`, `TradeExecution`
- No external message broker dependencies

### State & Config

- **SQLite** via `aiosqlite` for trade history, portfolio state, signal logs, price history. All DB writes are funneled through a single writer coroutine to avoid concurrent write contention (`database is locked` errors).
- **YAML config file** for all tunable parameters
- Sensitive values loadable from environment variables

## Signal Plugins

Each source implements a common interface:

```python
class SignalPlugin(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def evaluate(self, market: Market) -> Signal: ...
```

A `Signal` contains: `source`, `market_id`, `direction` (YES/NO), `confidence` (0.0-1.0), `reasoning`, `timestamp`.

### Plugin Inventory

| Plugin | Data Source | Method |
|--------|-----------|--------|
| **NewsSignal** | NewsAPI, Google News | Polls for articles related to active markets, keyword matching + LLM summarization |
| **SocialSignal** | Reddit API (primary), Twitter/X via third-party aggregator (optional) | Tracks sentiment on market-related topics, aggregates volume + sentiment score. Note: X API is cost-prohibitive ($5K/mo for meaningful volume); use Reddit as primary social source and optionally integrate a third-party X data provider (e.g., twitterapi.io) if budget allows. |
| **PollSignal** | RealClearPolitics, 270toWin, polling aggregators | Compares poll-implied probability vs. market price. Uses `realclearpolitics` PyPI package for RCP data. Note: FiveThirtyEight shut down in March 2025 and is no longer available. |
| **LLMSignal** | Claude/OpenAI API | Feeds market context to LLM, requests probability estimate with reasoning |
| **BookmakerSignal** | The Odds API | Converts bookmaker odds to implied probabilities, compares to Polymarket |

### Signal Aggregation

The Decision Engine computes a composite score using configurable weights:

```
composite = w_news * news + w_social * social + w_polls * polls + w_llm * llm + w_bookmaker * bookmaker
```

Signals that don't apply to a market are excluded and weights re-normalized.

## Arbitrage Engine

Runs independently from signal plugins, emitting its own events.

### Components

1. **Market Mapper** — Maintains mappings of equivalent markets across platforms. V1 uses manual config only. Future: LLM-assisted auto-matching with a human review queue (suggested matches require user approval via Telegram before activation; confidence threshold and matching prompt TBD in a future spec).
2. **Price Monitor** — Subscribes to Polymarket's WebSocket feed (`wss://ws-subscriptions-clob.polymarket.com`) for real-time order book data on the primary platform. Polls external platforms (Kalshi, Manifold, bookmakers) via REST at configurable interval (default: 30s). Stores price history in SQLite.
3. **Opportunity Detector** — Flags cross-platform price discrepancies and bookmaker divergences exceeding configurable threshold (default: 5%).

### Supported Platforms (as signal sources, not execution targets)

| Platform | Access Method | Notes |
|----------|--------------|-------|
| Kalshi | REST API | US-regulated, limited markets |
| Manifold | REST API | Play money, useful for sentiment |
| Bookmakers | The Odds API | Aggregates 50+ bookmakers |

**The bot only executes trades on Polymarket.** Cross-platform prices are used as signals only.

### Arb Event Payload

`ArbitrageOpportunity`: `market_ids`, `platforms`, `prices`, `spread`, `estimated_profit`, `confidence`, `time_sensitivity`.

## Decision Engine

Central brain that receives all signals and arbitrage opportunities.

### Decision Flow

1. Receive event
2. Lookup current portfolio exposure to this market
3. Aggregate all recent signals for this market (within configurable time window)
4. Calculate composite confidence score
5. Pass to Risk Manager for sizing and approval
6. If approved, emit `TradeDecision` (BUY/SELL, amount, market, side)

### Confidence Tiers (configurable)

| Tier | Confidence | Action |
|------|-----------|--------|
| High | >= 0.8 | Auto-execute |
| Medium | 0.5-0.8 | Notify and wait for user approval |
| Low | < 0.5 | Log only |

## Risk Manager

Validates every trade decision before execution.

| Rule | Default | Configurable |
|------|---------|:---:|
| Max position per market | 5% of bankroll | Yes |
| Max total exposure | 50% of bankroll | Yes |
| Max daily loss (circuit breaker) | 10% of bankroll | Yes |
| Max correlated exposure | 15% across related markets (correlation defined by manual tags in market config — e.g., markets sharing the same event category, geographic region, or underlying outcome are tagged as correlated) | Yes |
| Min edge required | 3% divergence from market price | Yes |
| Position sizing | Half-Kelly Criterion. Kelly inputs: `p` = composite confidence score, `b` = payout odds derived from current market price (e.g., YES at $0.30 → b = 0.70/0.30 ≈ 2.33). Final position size = `0.5 * ((p * b - (1 - p)) / b)` (half of the full Kelly fraction). **Bootstrapping**: for the first 50 trades (configurable), use flat 1% of bankroll sizing instead of Kelly, since confidence scores are uncalibrated. After threshold, begin Kelly with ongoing calibration of confidence-to-outcome accuracy. | Yes |
| Cooldown | 5 min no re-entry after exit | Yes |

Circuit breaker halts all trading and sends urgent notification when daily loss limit is hit.

## Execution Engine

Interacts with Polymarket's CLOB via `py-clob-client`.

### Capabilities

- **Order types**: Limit (default) and market (for time-sensitive arb)
- **Order management**: Place, cancel, amend. Track open orders and fill status.
- **Slippage protection**: Max slippage configurable (default: 1%). Reduces size or skips if orderbook depth insufficient.
- **Retry logic**: Exponential backoff, max 3 attempts for transient failures.
- **Execution logging**: Every order attempt, fill, and cancellation logged with full context.

### Trade Lifecycle

```
TradeDecision → Validate on-chain balance → Check orderbook depth
  → Place order → Monitor fill → Log result → Emit TradeExecution event
```

## Notification System

Plugin-based channels:

| Channel | Use Case |
|---------|----------|
| Telegram (primary) | Trade executions, approval requests, daily P&L, circuit breaker alerts |
| Discord (optional) | Same as Telegram |
| Email (optional) | Daily/weekly summary reports |

### Notification Types

- **Approval request** — Medium-confidence trade, includes signal breakdown and suggested size. User replies YES/NO via Telegram inline buttons. **Timeout**: configurable (default: 5 minutes). If no response within timeout, the pending decision is auto-cancelled and logged. An expiry notice is sent to the user.
- **Execution alert** — Trade placed/filled with price, size, expected edge.
- **Daily digest** — P&L, open positions, top signals, portfolio summary.
- **Urgent alert** — Circuit breaker triggered, API errors, anomalies.

## Database Schema (SQLite)

| Table | Purpose |
|-------|---------|
| trades | Full trade history: entry/exit price, size, P&L, triggering signals |
| signals | Every signal emitted with source, market, confidence, timestamp |
| markets | Cached market metadata + cross-platform mappings |
| portfolio | Current positions, balances, exposure |
| prices | Historical price snapshots across monitored platforms |
| orders | Order lifecycle (placed, partial fill, filled, cancelled) |

## Configuration

Single `config.yaml` with sections for Polymarket credentials, signal plugin settings (enable/disable, weights, poll intervals), risk parameters, execution settings, notification channels, and confidence thresholds.

Default signal weights: news 0.2, social 0.15, polls 0.25, LLM 0.25, bookmaker 0.15.

Sensitive values (API keys, private keys) loadable from environment variables as alternative to config file.

## CLI Interface

The bot uses `rich` for a colorful, polished terminal experience:

- **Live dashboard** — Real-time updating table showing open positions, active signals, P&L, and portfolio exposure using `rich.live`
- **Color-coded output** — Green for profits/buys, red for losses/sells, yellow for warnings, cyan for signals, magenta for arbitrage opportunities
- **Styled logging** — All log output uses `rich.logging` with color-coded levels and timestamps
- **Progress indicators** — Spinners for API calls, progress bars for initialization/data loading
- **Trade notifications** — Rich panels with borders for trade executions and approval requests in the terminal
- **Startup banner** — Stylized ASCII art banner with bot name and config summary on launch

## Key Design Decisions

1. **Python** — Best ecosystem for NLP, sentiment analysis, and Polymarket SDK support.
2. **Modular event-driven** — Plugin flexibility without microservice overhead.
3. **Polymarket-only execution** — Cross-platform data used as signals, not for cross-exchange arbitrage execution.
4. **Configurable automation** — High-confidence trades auto-execute, medium require approval, low are logged only.
5. **SQLite** — No external database dependency, sufficient for local single-bot operation.
6. **Half-Kelly default** — Conservative position sizing that balances growth with drawdown protection.
