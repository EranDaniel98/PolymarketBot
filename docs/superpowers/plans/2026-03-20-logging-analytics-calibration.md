# Logging, Analytics & Calibration Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist structured logs to disk for post-hoc analysis, track LLM confidence calibration, expand backtesting to all signals, and add a CLI analytics command.

**Architecture:** Add a JSON-lines file logger alongside the Rich console logger. Every signal evaluation, trade decision, risk check, and exit gets a structured JSON record. A new `analytics` CLI command reads these logs + the SQLite DB to produce calibration curves, signal P&L breakdowns, and fee impact analysis. The backtester is expanded to run all offline-capable signals (FLB, divergence, weather) instead of only FLB.

**Tech Stack:** Python stdlib `logging` (JSON formatter), SQLite (existing), Rich tables (existing CLI)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `polymarket_bot/logging_config.py` (create) | JSON-lines file handler + structured log formatter |
| `polymarket_bot/app.py` (modify) | Wire file logger alongside Rich handler |
| `polymarket_bot/config.py` (modify) | Add `LoggingConfig` dataclass |
| `config.yaml` (modify) | Add `logging:` section |
| `polymarket_bot/decision/engine.py` (modify) | Emit structured log on every decision |
| `polymarket_bot/decision/risk.py` (modify) | Emit structured log on every risk check |
| `polymarket_bot/exit_manager.py` (modify) | Emit structured log on every exit |
| `polymarket_bot/analytics.py` (create) | CLI analytics: calibration report, signal P&L, fee analysis |
| `polymarket_bot/__main__.py` (modify) | Add `analytics` CLI command |
| `polymarket_bot/backtesting/engine.py` (modify) | Run all offline signals, not just FLB |
| `polymarket_bot/database.py` (modify) | Add LLM calibration queries |
| Tests for each new module |

---

### Task 1: Structured JSON File Logger

**Files:**
- Create: `polymarket_bot/logging_config.py`
- Create: `tests/test_logging_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging_config.py
import json
import logging
import pytest
from pathlib import Path
from polymarket_bot.logging_config import setup_file_logging, StructuredLogger


def test_file_handler_writes_jsonl(tmp_path):
    log_file = tmp_path / "bot.jsonl"
    handler = setup_file_logging(log_file)
    logger = logging.getLogger("test_jsonl")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("test message", extra={"event_type": "test"})
    handler.flush()

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == "test message"
    assert record["event_type"] == "test"
    assert "timestamp" in record
    assert "level" in record


def test_structured_logger_signal():
    slog = StructuredLogger("test")
    extra = slog.signal_event(
        source="llm", market_id="m1", direction="YES",
        confidence=0.75, market_price=0.50,
    )
    assert extra["event_type"] == "signal"
    assert extra["source"] == "llm"
    assert extra["confidence"] == 0.75


def test_structured_logger_trade_decision():
    slog = StructuredLogger("test")
    extra = slog.trade_decision(
        market_id="m1", direction="YES", amount=25.0,
        confidence=0.85, action="auto_execute", signals=["llm", "whale"],
    )
    assert extra["event_type"] == "trade_decision"
    assert extra["action"] == "auto_execute"
    assert extra["signals"] == ["llm", "whale"]


def test_structured_logger_risk_check():
    slog = StructuredLogger("test")
    extra = slog.risk_check(
        market_id="m1", approved=False, reason="Circuit breaker",
        amount=25.0, edge=0.03,
    )
    assert extra["event_type"] == "risk_check"
    assert extra["approved"] is False


def test_structured_logger_exit():
    slog = StructuredLogger("test")
    extra = slog.exit_event(
        market_id="m1", reason="Take profit", pnl_pct=0.22,
        hours_held=8.5, direction="YES",
    )
    assert extra["event_type"] == "exit"
    assert extra["pnl_pct"] == 0.22
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logging_config.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# polymarket_bot/logging_config.py
"""Structured JSON-lines file logging for post-hoc analysis."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any structured extras
        for key in ("event_type", "source", "market_id", "direction",
                     "confidence", "market_price", "amount", "action",
                     "signals", "approved", "reason", "edge",
                     "pnl_pct", "hours_held", "category"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str)


def setup_file_logging(
    path: Path,
    level: int = logging.INFO,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Handler:
    """Create a rotating JSON-lines file handler."""
    from logging.handlers import RotatingFileHandler

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    return handler


class StructuredLogger:
    """Helper to emit structured log events with typed extras."""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def signal_event(self, source: str, market_id: str, direction: str,
                     confidence: float, market_price: float) -> dict:
        extra = {
            "event_type": "signal", "source": source,
            "market_id": market_id, "direction": direction,
            "confidence": confidence, "market_price": market_price,
        }
        self._logger.info(
            "Signal: %s %s conf=%.2f", source, direction, confidence,
            extra=extra,
        )
        return extra

    def trade_decision(self, market_id: str, direction: str, amount: float,
                       confidence: float, action: str,
                       signals: list[str]) -> dict:
        extra = {
            "event_type": "trade_decision", "market_id": market_id,
            "direction": direction, "amount": amount,
            "confidence": confidence, "action": action, "signals": signals,
        }
        self._logger.info(
            "Decision: %s %s $%.2f (%s)", direction, market_id[:16], amount, action,
            extra=extra,
        )
        return extra

    def risk_check(self, market_id: str, approved: bool, reason: str,
                   amount: float, edge: float) -> dict:
        extra = {
            "event_type": "risk_check", "market_id": market_id,
            "approved": approved, "reason": reason,
            "amount": amount, "edge": edge,
        }
        level = logging.INFO if approved else logging.WARNING
        self._logger.log(level, "Risk: %s — %s", market_id[:16], reason, extra=extra)
        return extra

    def exit_event(self, market_id: str, reason: str, pnl_pct: float,
                   hours_held: float, direction: str) -> dict:
        extra = {
            "event_type": "exit", "market_id": market_id,
            "reason": reason, "pnl_pct": pnl_pct,
            "hours_held": hours_held, "direction": direction,
        }
        self._logger.info(
            "Exit: %s %s pnl=%.1f%%", direction, market_id[:16], pnl_pct * 100,
            extra=extra,
        )
        return extra
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logging_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/logging_config.py tests/test_logging_config.py
git commit -m "feat: structured JSON-lines file logger with typed event helpers"
```

---

### Task 2: Wire File Logger & Add Config

**Files:**
- Modify: `polymarket_bot/config.py` (add LoggingConfig)
- Modify: `config.yaml` (add logging section)
- Modify: `polymarket_bot/app.py` (wire file handler + StructuredLogger)

- [ ] **Step 1: Add LoggingConfig to config.py**

Add after `ExitConfig`:

```python
@dataclass
class LoggingConfig:
    file_enabled: bool = True
    file_path: str = "logs/bot.jsonl"
    max_size_mb: int = 50
    backup_count: int = 5
```

Add `logging: LoggingConfig = None` to `BotConfig` and `self.logging = self.logging or LoggingConfig()` in `__post_init__`.

- [ ] **Step 2: Add logging section to config.yaml**

```yaml
logging:
  file_enabled: true
  file_path: "logs/bot.jsonl"
  max_size_mb: 50
  backup_count: 5
```

- [ ] **Step 3: Wire file handler in app.py**

In `setup_logging()`, add after the Rich handler setup:

```python
from polymarket_bot.logging_config import setup_file_logging
# (called later inside run_bot after config is loaded)
```

In `run_bot()`, after config is loaded, before any business logic:

```python
if config.logging.file_enabled:
    from polymarket_bot.logging_config import setup_file_logging
    file_handler = setup_file_logging(
        Path(config.logging.file_path),
        max_bytes=config.logging.max_size_mb * 1024 * 1024,
        backup_count=config.logging.backup_count,
    )
    logging.getLogger().addHandler(file_handler)
    console.print(f"[bold green]File logging:[/] {config.logging.file_path}")
```

Create a shared `StructuredLogger` instance:

```python
from polymarket_bot.logging_config import StructuredLogger
slog = StructuredLogger("polymarket_bot.structured")
```

- [ ] **Step 4: Add structured logging calls to decision engine**

In `polymarket_bot/decision/engine.py`, in `on_signal()`:
- After `size = await self._risk.calculate_position_size(...)` (right before creating the `TradeDecision`), add:

```python
# Structured log for analytics
import logging as _logging
_slog = _logging.getLogger("polymarket_bot.structured")
_slog.info(
    "Decision: %s %s $%.2f (%s)", direction.value, market.id[:16], size, action,
    extra={
        "event_type": "trade_decision",
        "market_id": market.id,
        "direction": direction.value,
        "amount": size,
        "confidence": composite,
        "action": action,
        "signals": [s.source for s in recent_signals],
        "market_price": market.current_price,
    },
)
```

Note: `log_only` actions return early before `direction` and `size` are computed, so this log only captures `auto_execute` and `notify` decisions. That's intentional — log_only events have no trade to analyze.

- [ ] **Step 5: Add structured logging to risk manager**

In `polymarket_bot/decision/risk.py`, at the end of `check()`, before each `return`:
- Log via `logger` with extra dict containing `event_type="risk_check"`.

- [ ] **Step 6: Add structured logging to exit manager**

In `polymarket_bot/exit_manager.py`, in `_trigger_exit()`, compute P&L from the price getter before logging:

```python
# Compute pnl_pct for structured logging
pnl_pct = 0.0
if self._price_getter:
    current_price = self._price_getter("polymarket", pos.market_id)
    if current_price is not None:
        if pos.direction == Direction.YES:
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - current_price) / pos.entry_price

hours_held = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600

logger.info(
    "Exit: %s %s", pos.direction.value, pos.market_id[:16],
    extra={
        "event_type": "exit",
        "market_id": pos.market_id,
        "reason": reason,
        "pnl_pct": pnl_pct,
        "hours_held": hours_held,
        "direction": pos.direction.value,
    },
)
```

Place this at the top of `_trigger_exit()`, before the `console.print` call.

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all pass (206+)

- [ ] **Step 8: Commit**

```bash
git add polymarket_bot/config.py config.yaml polymarket_bot/app.py polymarket_bot/decision/engine.py polymarket_bot/decision/risk.py polymarket_bot/exit_manager.py
git commit -m "feat: wire structured JSON file logging to decision engine, risk, and exits"
```

---

### Task 3: LLM Calibration Tracking Queries

**Files:**
- Modify: `polymarket_bot/database.py` (add calibration-specific queries)
- Create: `tests/test_calibration_queries.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_calibration_queries.py
import pytest
from datetime import datetime, timezone
from polymarket_bot.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_get_confidence_calibration(db):
    """Bin signals by confidence bucket and compute actual accuracy per bucket."""
    now = datetime.now(timezone.utc)
    # 5 signals at ~0.60 confidence, 3 correct
    for i in range(5):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.60, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 3 else "No")

    # 5 signals at ~0.85 confidence, 4 correct
    for i in range(5, 10):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.85, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 9 else "No")

    buckets = await db.get_confidence_calibration("llm", bucket_size=0.20)
    assert len(buckets) >= 2
    # 0.50-0.70 bucket should have ~60% accuracy
    low_bucket = next(b for b in buckets if b["bucket_min"] <= 0.60 < b["bucket_max"])
    assert low_bucket["accuracy"] == pytest.approx(0.6, abs=0.01)
    # 0.80-1.00 bucket should have ~80% accuracy
    high_bucket = next(b for b in buckets if b["bucket_min"] <= 0.85 < b["bucket_max"])
    assert high_bucket["accuracy"] == pytest.approx(0.8, abs=0.01)


async def test_get_confidence_gap(db):
    """Compute avg predicted confidence vs actual accuracy for a source."""
    now = datetime.now(timezone.utc)
    for i in range(10):
        await db.save_signal_outcome("llm", f"m{i}", "YES", 0.80, 0.45, now)
        await db.record_resolution(f"m{i}", "Yes" if i < 6 else "No")

    gap = await db.get_confidence_gap("llm")
    assert gap is not None
    assert gap["avg_confidence"] == pytest.approx(0.80, abs=0.01)
    assert gap["actual_accuracy"] == pytest.approx(0.60, abs=0.01)
    assert gap["gap"] == pytest.approx(0.20, abs=0.01)


async def test_get_fee_impact_report(db):
    """Compute total fees paid as fraction of P&L."""
    from polymarket_bot.models import TradeExecution, Direction, OrderStatus
    for i in range(5):
        trade = TradeExecution(
            market_id=f"m{i}", direction=Direction.YES, amount=25.0,
            price=0.50, order_id=f"ord{i}", status=OrderStatus.FILLED,
            fees=0.50, realized_pnl=2.0 if i < 3 else -1.0,
        )
        await db.save_trade(trade)

    report = await db.get_fee_impact_report()
    assert report["total_fees"] == pytest.approx(2.50, abs=0.01)
    assert report["total_pnl"] == pytest.approx(4.0, abs=0.01)  # 3*2 + 2*-1 = 4
    assert report["fee_pct_of_volume"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calibration_queries.py -v`
Expected: FAIL — methods not found

- [ ] **Step 3: Implement calibration queries in database.py**

Add to `Database` class:

```python
async def get_confidence_calibration(
    self, source: str, bucket_size: float = 0.10,
) -> list[dict]:
    """Bin resolved signals by confidence and compute actual accuracy per bucket."""
    rows = await self._fetch_all(
        "SELECT confidence, was_correct FROM signal_outcomes "
        "WHERE source = ? AND was_correct IS NOT NULL",
        (source,),
    )
    if not rows:
        return []
    from collections import defaultdict
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in rows:
        conf = row["confidence"]
        bucket_idx = int(conf / bucket_size)
        bucket_min = round(bucket_idx * bucket_size, 2)
        buckets[bucket_min]["total"] += 1
        if row["was_correct"]:
            buckets[bucket_min]["correct"] += 1
    result = []
    for bucket_min in sorted(buckets):
        b = buckets[bucket_min]
        result.append({
            "bucket_min": bucket_min,
            "bucket_max": round(bucket_min + bucket_size, 2),
            "total": b["total"],
            "correct": b["correct"],
            "accuracy": b["correct"] / b["total"] if b["total"] > 0 else 0,
        })
    return result

async def get_confidence_gap(self, source: str) -> dict | None:
    """Compute the gap between average predicted confidence and actual accuracy."""
    row = await self._fetch_one(
        "SELECT AVG(confidence) as avg_conf, "
        "AVG(CASE WHEN was_correct = 1 THEN 1.0 ELSE 0.0 END) as accuracy, "
        "COUNT(*) as n "
        "FROM signal_outcomes WHERE source = ? AND was_correct IS NOT NULL",
        (source,),
    )
    if not row or row["n"] == 0:
        return None
    avg_conf = row["avg_conf"]
    accuracy = row["accuracy"]
    return {
        "avg_confidence": avg_conf,
        "actual_accuracy": accuracy,
        "gap": avg_conf - accuracy,
        "n_signals": row["n"],
    }

async def get_fee_impact_report(self) -> dict:
    """Compute total fees as fraction of trading volume."""
    row = await self._fetch_one(
        "SELECT COALESCE(SUM(fees), 0) as total_fees, "
        "COALESCE(SUM(realized_pnl), 0) as total_pnl, "
        "COALESCE(SUM(amount), 0) as total_volume, "
        "COUNT(*) as n_trades "
        "FROM trades WHERE status = 'filled'"
    )
    total_vol = row["total_volume"] if row["total_volume"] else 1
    return {
        "total_fees": row["total_fees"],
        "total_pnl": row["total_pnl"],
        "total_volume": row["total_volume"],
        "n_trades": row["n_trades"],
        "fee_pct_of_volume": row["total_fees"] / total_vol if total_vol > 0 else 0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_calibration_queries.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/database.py tests/test_calibration_queries.py
git commit -m "feat: add confidence calibration, gap, and fee impact queries"
```

---

### Task 4: CLI Analytics Command

**Files:**
- Create: `polymarket_bot/analytics.py`
- Modify: `polymarket_bot/__main__.py` (add `analytics` command)

- [ ] **Step 1: Write the analytics module**

```python
# polymarket_bot/analytics.py
"""CLI analytics — reads DB + log files to produce actionable reports."""

import json
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from polymarket_bot.database import Database

logger = logging.getLogger(__name__)
console = Console()


async def run_analytics(db_path: str = "polymarket_bot.db", log_path: str = "logs/bot.jsonl"):
    db = Database(Path(db_path))
    await db.initialize()

    try:
        console.print("\n[bold cyan]===  Polymarket Bot Analytics  ===[/]\n")

        # 1. Overall performance
        await _print_performance(db)

        # 2. Signal accuracy report
        await _print_signal_accuracy(db)

        # 3. LLM confidence calibration
        await _print_calibration(db)

        # 4. Fee impact
        await _print_fee_impact(db)

        # 5. Log file event summary
        _print_log_summary(Path(log_path))

    finally:
        await db.close()


async def _print_performance(db: Database) -> None:
    total_pnl = await db.get_total_pnl()
    win_rate = await db.get_win_rate()
    trade_count = await db.get_trade_count()

    table = Table(title="Overall Performance", border_style="cyan")
    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")

    pnl_style = "green" if total_pnl >= 0 else "red"
    table.add_row("Total Trades", str(trade_count))
    table.add_row("Win Rate", f"{win_rate:.1%}")
    table.add_row("Total P&L", f"[{pnl_style}]${total_pnl:+.2f}[/]")
    console.print(table)
    console.print()


async def _print_signal_accuracy(db: Database) -> None:
    report = await db.get_accuracy_report()
    if not report:
        console.print("[dim]No signal accuracy data yet (markets need to resolve)[/]\n")
        return

    table = Table(title="Signal Accuracy (resolved markets only)", border_style="cyan")
    table.add_column("Signal", style="white")
    table.add_column("Signals", justify="right")
    table.add_column("Accuracy", justify="right")
    table.add_column("Avg Confidence", justify="right")
    table.add_column("Conf Gap", justify="right")

    for source, stats in sorted(report.items()):
        gap = stats["avg_confidence"] - stats["accuracy"] if stats["avg_confidence"] else 0
        gap_style = "red" if gap > 0.10 else "yellow" if gap > 0.05 else "green"
        table.add_row(
            source,
            str(stats["n_signals"]),
            f"{stats['accuracy']:.1%}",
            f"{stats['avg_confidence']:.1%}" if stats["avg_confidence"] else "N/A",
            f"[{gap_style}]{gap:+.1%}[/]",
        )
    console.print(table)
    console.print()


async def _print_calibration(db: Database) -> None:
    # Check LLM specifically since it's the most important signal
    for source in ("llm", "favorite_longshot", "divergence", "weather"):
        gap = await db.get_confidence_gap(source)
        if not gap or gap["n_signals"] < 5:
            continue

        buckets = await db.get_confidence_calibration(source, bucket_size=0.20)
        if not buckets:
            continue

        table = Table(title=f"Calibration: {source} (n={gap['n_signals']})", border_style="magenta")
        table.add_column("Confidence Bucket", style="white")
        table.add_column("Signals", justify="right")
        table.add_column("Actual Accuracy", justify="right")
        table.add_column("Status", justify="right")

        for b in buckets:
            expected_mid = (b["bucket_min"] + b["bucket_max"]) / 2
            diff = b["accuracy"] - expected_mid
            if abs(diff) < 0.10:
                status = "[green]Well calibrated[/]"
            elif diff > 0:
                status = "[cyan]Underconfident[/]"
            else:
                status = "[red]Overconfident[/]"
            table.add_row(
                f"{b['bucket_min']:.0%}-{b['bucket_max']:.0%}",
                str(b["total"]),
                f"{b['accuracy']:.0%}",
                status,
            )
        console.print(table)

        # Print gap summary
        gap_style = "red" if gap["gap"] > 0.10 else "yellow" if gap["gap"] > 0.05 else "green"
        console.print(
            f"  [{gap_style}]Confidence gap: {gap['gap']:+.1%}[/] "
            f"(predicted {gap['avg_confidence']:.0%} vs actual {gap['actual_accuracy']:.0%})\n"
        )


async def _print_fee_impact(db: Database) -> None:
    report = await db.get_fee_impact_report()
    if report["n_trades"] == 0:
        return

    table = Table(title="Fee Impact Analysis", border_style="yellow")
    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")

    table.add_row("Total Volume", f"${report['total_volume']:.2f}")
    table.add_row("Total Fees", f"[red]${report['total_fees']:.2f}[/]")
    table.add_row("Fees as % of Volume", f"{report['fee_pct_of_volume']:.2%}")
    table.add_row("Net P&L (after fees)", f"${report['total_pnl']:.2f}")

    # Breakeven analysis
    if report["n_trades"] > 0:
        avg_trade = report["total_volume"] / report["n_trades"]
        min_profitable = report["total_fees"] / report["n_trades"] if report["n_trades"] > 0 else 0
        table.add_row("Avg Trade Size", f"${avg_trade:.2f}")
        table.add_row("Avg Fee per Trade", f"${min_profitable:.2f}")

    console.print(table)
    console.print()


def _print_log_summary(log_path: Path) -> None:
    if not log_path.exists():
        console.print("[dim]No log file found — run the bot with file logging enabled[/]\n")
        return

    counts = {}
    total = 0
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
            event_type = record.get("event_type", "other")
            counts[event_type] = counts.get(event_type, 0) + 1
            total += 1
        except json.JSONDecodeError:
            continue

    if not counts:
        console.print("[dim]Log file empty[/]\n")
        return

    table = Table(title=f"Log Events ({total} total)", border_style="green")
    table.add_column("Event Type", style="white")
    table.add_column("Count", justify="right")

    for event_type, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(event_type, str(count))
    console.print(table)
    console.print()
```

- [ ] **Step 2: Add `analytics` command to __main__.py**

Change the `choices` to include `"analytics"`:

```python
choices=["run", "backtest", "analytics"],
```

Add the handler:

```python
elif args.command == "analytics":
    from polymarket_bot.analytics import run_analytics
    try:
        asyncio.run(run_analytics())
    except KeyboardInterrupt:
        sys.exit(0)
```

- [ ] **Step 3: Test manually**

Run: `python -m polymarket_bot analytics`
Expected: tables print (may be empty if no data yet)

- [ ] **Step 4: Commit**

```bash
git add polymarket_bot/analytics.py polymarket_bot/__main__.py
git commit -m "feat: add CLI analytics command with calibration, accuracy, and fee reports"
```

---

### Task 5: Expand Backtester to All Offline Signals

**Files:**
- Modify: `polymarket_bot/backtesting/engine.py`
- Modify: `tests/test_backtesting/test_engine.py`

- [ ] **Step 1: Write failing test for multi-signal backtest**

```python
# Add to tests/test_backtesting/test_engine.py

async def test_backtest_runs_multiple_signals():
    """Backtest should evaluate FLB + divergence + weather, not just FLB."""
    from polymarket_bot.backtesting.engine import BacktestEngine, build_offline_signals
    plugins = await build_offline_signals()
    assert len(plugins) >= 2  # At least FLB + one more
    names = [p.name for p in plugins]
    assert "favorite_longshot" in names
    # Clean up
    for p in plugins:
        await p.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backtesting/test_engine.py::test_backtest_runs_multiple_signals -v`
Expected: FAIL — `build_offline_signals` not found

- [ ] **Step 3: Refactor backtesting engine**

Extract signal initialization into `build_offline_signals()` and use all offline-capable signals in `run_backtest()`:

```python
# Add to polymarket_bot/backtesting/engine.py

async def build_offline_signals() -> list:
    """Build all signal plugins that work without live API keys."""
    from polymarket_bot.signals.favorite_longshot import FavoriteLongshotSignal
    from polymarket_bot.signals.divergence import DivergenceSignal
    from polymarket_bot.signals.weather import WeatherSignal

    plugins = [
        FavoriteLongshotSignal(),
        DivergenceSignal(),
        WeatherSignal(),
    ]
    for p in plugins:
        await p.start()
    return plugins
```

Update `run_backtest()`:
- Replace the single `flb = FavoriteLongshotSignal()` with `plugins = await build_offline_signals()`
- Evaluate ALL plugins against each market
- Pick the best signal across all plugins
- Stop all plugins at the end

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backtesting/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/backtesting/engine.py tests/test_backtesting/test_engine.py
git commit -m "feat: expand backtester to run FLB + divergence + weather signals"
```

---

### Task 6: Fee-Aware Minimum Trade Size

**Files:**
- Modify: `polymarket_bot/decision/risk.py`
- Modify: `polymarket_bot/config.py`
- Modify: `tests/test_decision/test_risk.py`

The $300 bankroll means small trades where fees dominate. Add a minimum trade size that ensures fees don't exceed a % of the trade.

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_decision/test_risk.py

async def test_minimum_trade_size_enforced(mock_db):
    """Trades too small for fees to be worthwhile should be rejected."""
    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=50,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
        min_trade_size=10.0,
    )
    rm = RiskManager(config=config, database=mock_db, bankroll=5000.0)
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=5.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await rm.check(decision, market_price=0.30)
    assert approved is False
    assert "minimum" in reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_decision/test_risk.py::test_minimum_trade_size_enforced -v`
Expected: FAIL

- [ ] **Step 3: Add min_trade_size to RiskConfig**

```python
# In config.py RiskConfig, add:
min_trade_size: float = 10.0
```

- [ ] **Step 4: Add minimum size check to risk.py check()**

After the max position check, before correlated exposure:

```python
# Minimum trade size — ensures fees don't dominate
if decision.amount < self._config.min_trade_size:
    return False, f"Below minimum trade size: ${decision.amount:.2f} < ${self._config.min_trade_size:.2f}"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_decision/test_risk.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add polymarket_bot/config.py polymarket_bot/decision/risk.py tests/test_decision/test_risk.py
git commit -m "feat: add minimum trade size to prevent fee-dominated tiny trades"
```

---

### Task 7: Add .gitignore for Logs Directory

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add logs directory to .gitignore**

```
# Log files
logs/
*.jsonl
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore logs directory"
```

---

## Verification Checklist

After all tasks:

1. `python -m pytest tests/ -v` — all pass
2. `python -m polymarket_bot run` — bot starts, `logs/bot.jsonl` gets created and populated
3. `python -m polymarket_bot analytics` — prints calibration tables (may be empty initially)
4. `python -m polymarket_bot backtest --days 30` — runs with 3 signals instead of 1
5. Check `logs/bot.jsonl` — each line is valid JSON with `event_type` field
6. After 1-2 weeks of paper trading: re-run `analytics` to see calibration gaps and fee impact

## About the Bankroll

Re: should you add more funds — the analytics command (Task 4) will answer this empirically. Run it after 2 weeks of paper trading. If the fee impact report shows fees > 30% of gross P&L, the math says either:
- **Increase bankroll** to $1000+ (larger trades amortize fixed costs better)
- **Increase min_edge** further (only take trades where edge clearly exceeds fees)
- **Reduce trade frequency** (fewer, higher-conviction trades)

The data will tell you which lever to pull.
