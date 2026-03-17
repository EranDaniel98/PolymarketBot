# Polymarket Trading Bot Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python trading bot for Polymarket that uses multi-source signals and cross-platform arbitrage detection, with configurable risk management and Telegram notifications.

**Architecture:** Modular event-driven architecture with 7 core modules communicating through an in-process async event bus. Each module is a separate package with clean interfaces. SQLite for persistence, YAML for config, `rich` for CLI.

**Tech Stack:** Python 3.12+, asyncio, aiosqlite, py-clob-client, websockets, rich, python-telegram-bot, pyyaml, httpx, anthropic SDK, pydantic, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-17-polymarket-bot-design.md`

---

## File Structure

```
polymarket_bot/
├── __init__.py
├── __main__.py                 # Entry point, CLI args
├── app.py                      # Main orchestrator, wires all modules
├── config.py                   # YAML + env var config loading via pydantic
├── models.py                   # All shared dataclasses/models
├── event_bus.py                # Async pub/sub event bus
├── database.py                 # aiosqlite wrapper, single writer coroutine
├── cli.py                      # Rich dashboard, logging, banner
├── signals/
│   ├── __init__.py
│   ├── base.py                 # SignalPlugin ABC
│   ├── news.py                 # NewsSignal plugin
│   ├── social.py               # SocialSignal plugin (Reddit)
│   ├── polls.py                # PollSignal plugin (RCP)
│   ├── llm.py                  # LLMSignal plugin (Claude/OpenAI)
│   └── bookmaker.py            # BookmakerSignal plugin (The Odds API)
├── arbitrage/
│   ├── __init__.py
│   ├── mapper.py               # Manual market mapper
│   ├── monitor.py              # Price monitor (WS for Polymarket, REST for others)
│   └── detector.py             # Opportunity detector
├── decision/
│   ├── __init__.py
│   ├── engine.py               # Decision engine, signal aggregation
│   └── risk.py                 # Risk manager, Kelly sizing, circuit breaker
├── execution/
│   ├── __init__.py
│   └── engine.py               # Order placement via py-clob-client
├── notifications/
│   ├── __init__.py
│   ├── base.py                 # Notifier ABC
│   ├── telegram.py             # Telegram bot with inline approval buttons
│   └── discord.py              # Discord webhook notifier
tests/
├── conftest.py                 # Shared fixtures (event bus, db, config)
├── test_models.py
├── test_event_bus.py
├── test_config.py
├── test_database.py
├── test_cli.py
├── test_signals/
│   ├── conftest.py
│   ├── test_base.py
│   ├── test_news.py
│   ├── test_social.py
│   ├── test_polls.py
│   ├── test_llm.py
│   └── test_bookmaker.py
├── test_arbitrage/
│   ├── test_mapper.py
│   ├── test_monitor.py
│   └── test_detector.py
├── test_decision/
│   ├── test_engine.py
│   └── test_risk.py
├── test_execution/
│   └── test_engine.py
├── test_notifications/
│   ├── test_telegram.py
│   └── test_discord.py
└── test_integration.py
config.example.yaml
pyproject.toml
.gitignore
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.example.yaml`
- Create: `polymarket_bot/__init__.py`
- Create: `polymarket_bot/__main__.py`

- [ ] **Step 1: Initialize git repo**

```bash
cd C:/Users/Eran/Desktop/Personal/PolymarketBot
git init
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "polymarket-bot"
version = "0.1.0"
description = "Signal-based trading bot for Polymarket with cross-platform arbitrage detection"
requires-python = ">=3.12"
dependencies = [
    "aiosqlite>=0.20.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    "py-clob-client>=0.17.0",
    "python-telegram-bot>=21.0",
    "pyyaml>=6.0",
    "rich>=13.9.0",
    "websockets>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "pytest-mock>=3.14.0",
    "ruff>=0.8.0",
]

[project.scripts]
polymarket-bot = "polymarket_bot.__main__:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 100
```

- [ ] **Step 3: Create .gitignore**

```
__pycache__/
*.pyc
.venv/
*.egg-info/
dist/
build/
config.yaml
.env
*.db
.ruff_cache/
.pytest_cache/
```

- [ ] **Step 4: Create config.example.yaml**

```yaml
polymarket:
  api_key: "your-api-key"
  api_secret: "your-api-secret"
  private_key: "your-eth-private-key"
  chain_id: 137  # Polygon

signals:
  news:
    enabled: true
    poll_interval: 300
    weight: 0.2
    newsapi_key: "your-newsapi-key"
  social:
    enabled: true
    poll_interval: 600
    weight: 0.15
    reddit_client_id: "your-reddit-client-id"
    reddit_client_secret: "your-reddit-client-secret"
  polls:
    enabled: true
    poll_interval: 3600
    weight: 0.25
  llm:
    enabled: true
    weight: 0.25
    model: "claude-sonnet-4-6-20250514"
    anthropic_api_key: "your-anthropic-key"
  bookmaker:
    enabled: true
    poll_interval: 60
    weight: 0.15
    odds_api_key: "your-odds-api-key"

arbitrage:
  poll_interval: 30
  min_spread: 0.05
  platforms:
    kalshi:
      enabled: true
    manifold:
      enabled: true

risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300

execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3

notifications:
  telegram:
    enabled: true
    bot_token: "your-bot-token"
    chat_id: "your-chat-id"
    approval_timeout: 300
  discord:
    enabled: false
    webhook_url: ""

confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
```

- [ ] **Step 5: Create package init and entry point**

`polymarket_bot/__init__.py`:
```python
"""Polymarket Trading Bot — Signal-based trading with cross-platform arbitrage detection."""

__version__ = "0.1.0"
```

`polymarket_bot/__main__.py`:
```python
import asyncio
import sys

def main():
    from polymarket_bot.app import run_bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Create empty app.py placeholder**

`polymarket_bot/app.py`:
```python
async def run_bot():
    """Main entry point — wired up in later tasks."""
    raise NotImplementedError("Bot not yet wired up")
```

- [ ] **Step 7: Set up virtual environment and install**

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
pip install -e ".[dev]"
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore config.example.yaml polymarket_bot/
git commit -m "feat: project scaffold with dependencies and config template"
```

---

## Task 2: Core Models

**Files:**
- Create: `polymarket_bot/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for models**

`tests/test_models.py`:
```python
import pytest
from datetime import datetime, timezone
from polymarket_bot.models import (
    Direction, Signal, Market, TradeDecision, TradeExecution,
    ArbitrageOpportunity, OrderType, OrderStatus, SignalEvent,
)


def test_signal_confidence_must_be_between_0_and_1():
    with pytest.raises(ValueError):
        Signal(source="test", market_id="m1", direction=Direction.YES,
               confidence=1.5, reasoning="test", timestamp=datetime.now(timezone.utc))

    with pytest.raises(ValueError):
        Signal(source="test", market_id="m1", direction=Direction.YES,
               confidence=-0.1, reasoning="test", timestamp=datetime.now(timezone.utc))


def test_signal_valid():
    s = Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.75, reasoning="Strong signal", timestamp=datetime.now(timezone.utc))
    assert s.source == "news"
    assert s.confidence == 0.75


def test_market_model():
    m = Market(id="0x123", question="Will X happen?", end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
               tokens={"YES": "0xabc", "NO": "0xdef"}, current_price=0.55)
    assert m.id == "0x123"
    assert m.current_price == 0.55


def test_trade_decision():
    td = TradeDecision(market_id="m1", direction=Direction.YES, amount=50.0,
                       confidence=0.85, signals=[], order_type=OrderType.LIMIT)
    assert td.amount == 50.0
    assert td.order_type == OrderType.LIMIT


def test_arbitrage_opportunity():
    arb = ArbitrageOpportunity(
        market_ids={"polymarket": "m1", "kalshi": "k1"},
        platforms=["polymarket", "kalshi"],
        prices={"polymarket": 0.45, "kalshi": 0.55},
        spread=0.10,
        estimated_profit=5.0,
        confidence=0.9,
        time_sensitivity="high",
    )
    assert arb.spread == 0.10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_models.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'polymarket_bot.models'`

- [ ] **Step 3: Implement models**

`polymarket_bot/models.py`:
```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Direction(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PLACED = "placed"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Signal:
    source: str
    market_id: str
    direction: Direction
    confidence: float
    reasoning: str
    timestamp: datetime

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")


@dataclass
class Market:
    id: str
    question: str
    end_date: datetime
    tokens: dict[str, str]
    current_price: float
    category: str = ""
    correlation_tags: list[str] = field(default_factory=list)
    platform_mappings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalEvent:
    signal: Signal
    market: Market


@dataclass(frozen=True)
class ArbitrageOpportunity:
    market_ids: dict[str, str]
    platforms: list[str]
    prices: dict[str, float]
    spread: float
    estimated_profit: float
    confidence: float
    time_sensitivity: str


@dataclass
class TradeDecision:
    market_id: str
    direction: Direction
    amount: float
    confidence: float
    signals: list[Signal]
    order_type: OrderType
    arb_opportunity: ArbitrageOpportunity | None = None


@dataclass
class TradeExecution:
    market_id: str
    direction: Direction
    amount: float
    price: float
    order_id: str
    status: OrderStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fees: float = 0.0
    realized_pnl: float = 0.0
    error: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_models.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/models.py tests/test_models.py
git commit -m "feat: core data models with validation"
```

---

## Task 3: Event Bus

**Files:**
- Create: `polymarket_bot/event_bus.py`
- Create: `tests/test_event_bus.py`

- [ ] **Step 1: Write failing tests**

`tests/test_event_bus.py`:
```python
import pytest
import asyncio
from polymarket_bot.event_bus import EventBus


@pytest.fixture
def bus():
    return EventBus()


async def test_subscribe_and_publish(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("test_event", handler)
    await bus.publish("test_event", {"data": "hello"})
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0]["data"] == "hello"


async def test_multiple_subscribers(bus):
    received_a = []
    received_b = []

    async def handler_a(event):
        received_a.append(event)

    async def handler_b(event):
        received_b.append(event)

    bus.subscribe("evt", handler_a)
    bus.subscribe("evt", handler_b)
    await bus.publish("evt", "payload")
    await asyncio.sleep(0.05)
    assert len(received_a) == 1
    assert len(received_b) == 1


async def test_unsubscribe(bus):
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    await bus.publish("evt", "payload")
    await asyncio.sleep(0.05)
    assert len(received) == 0


async def test_publish_no_subscribers(bus):
    # Should not raise
    await bus.publish("nobody_listening", "data")


async def test_handler_error_does_not_break_others(bus):
    received = []

    async def bad_handler(event):
        raise ValueError("boom")

    async def good_handler(event):
        received.append(event)

    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    await bus.publish("evt", "data")
    await asyncio.sleep(0.05)
    assert len(received) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_event_bus.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement event bus**

`polymarket_bot/event_bus.py`:
```python
import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event_type: str, event: Any) -> None:
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Handler %s failed for event %s", handler.__name__, event_type)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_event_bus.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/event_bus.py tests/test_event_bus.py
git commit -m "feat: async event bus with pub/sub"
```

---

## Task 4: Configuration

**Files:**
- Create: `polymarket_bot/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import pytest
from pathlib import Path
from polymarket_bot.config import load_config, BotConfig


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
polymarket:
  api_key: "test-key"
  api_secret: "test-secret"
  private_key: "0xdeadbeef"
  chain_id: 137
signals:
  news:
    enabled: true
    poll_interval: 300
    weight: 0.2
    newsapi_key: "nk"
  social:
    enabled: false
    poll_interval: 600
    weight: 0.15
  polls:
    enabled: false
    poll_interval: 3600
    weight: 0.25
  llm:
    enabled: false
    weight: 0.25
    model: "claude-sonnet-4-6-20250514"
  bookmaker:
    enabled: false
    poll_interval: 60
    weight: 0.15
risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300
execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3
notifications:
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""
    approval_timeout: 300
  discord:
    enabled: false
    webhook_url: ""
confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
arbitrage:
  poll_interval: 30
  min_spread: 0.05
""")
    config = load_config(config_file)
    assert isinstance(config, BotConfig)
    assert config.polymarket.api_key == "test-key"
    assert config.risk.kelly_fraction == 0.5
    assert config.signals.news.weight == 0.2


def test_load_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
polymarket:
  api_key: "from-file"
  api_secret: "s"
  private_key: "0x1"
  chain_id: 137
signals:
  news: {enabled: false, poll_interval: 300, weight: 0.2}
  social: {enabled: false, poll_interval: 600, weight: 0.15}
  polls: {enabled: false, poll_interval: 3600, weight: 0.25}
  llm: {enabled: false, weight: 0.25, model: "claude-sonnet-4-6-20250514"}
  bookmaker: {enabled: false, poll_interval: 60, weight: 0.15}
risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300
execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3
notifications:
  telegram: {enabled: false, bot_token: "", chat_id: "", approval_timeout: 300}
  discord: {enabled: false, webhook_url: ""}
confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
arbitrage:
  poll_interval: 30
  min_spread: 0.05
""")
    monkeypatch.setenv("POLYMARKET_API_KEY", "from-env")
    config = load_config(config_file)
    assert config.polymarket.api_key == "from-env"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement config**

`polymarket_bot/config.py`:
```python
import os
from pathlib import Path
from dataclasses import dataclass, fields

import yaml


class _SecretStr(str):
    """String that masks itself in repr/str to prevent accidental secret leakage."""
    def __repr__(self) -> str:
        return "'**********'" if self else "''"
    def __str__(self) -> str:
        return super().__str__()  # Full value when explicitly cast to str
    def secret_value(self) -> str:
        return super().__str__()


@dataclass
class PolymarketConfig:
    api_key: str = ""
    api_secret: str = ""
    private_key: str = ""
    chain_id: int = 137

    def __post_init__(self):
        if self.private_key:
            self.private_key = _SecretStr(self.private_key)
        if self.api_secret:
            self.api_secret = _SecretStr(self.api_secret)


@dataclass
class NewsSignalConfig:
    enabled: bool = True
    poll_interval: int = 300
    weight: float = 0.2
    newsapi_key: str = ""


@dataclass
class SocialSignalConfig:
    enabled: bool = True
    poll_interval: int = 600
    weight: float = 0.15
    reddit_client_id: str = ""
    reddit_client_secret: str = ""


@dataclass
class PollSignalConfig:
    enabled: bool = True
    poll_interval: int = 3600
    weight: float = 0.25


@dataclass
class LLMSignalConfig:
    enabled: bool = True
    weight: float = 0.25
    model: str = "claude-sonnet-4-6-20250514"
    anthropic_api_key: str = ""


@dataclass
class BookmakerSignalConfig:
    enabled: bool = True
    poll_interval: int = 60
    weight: float = 0.15
    odds_api_key: str = ""


@dataclass
class SignalsConfig:
    news: NewsSignalConfig = None
    social: SocialSignalConfig = None
    polls: PollSignalConfig = None
    llm: LLMSignalConfig = None
    bookmaker: BookmakerSignalConfig = None

    def __post_init__(self):
        self.news = self.news or NewsSignalConfig()
        self.social = self.social or SocialSignalConfig()
        self.polls = self.polls or PollSignalConfig()
        self.llm = self.llm or LLMSignalConfig()
        self.bookmaker = self.bookmaker or BookmakerSignalConfig()


@dataclass
class RiskConfig:
    max_position_pct: float = 0.05
    max_exposure_pct: float = 0.50
    max_daily_loss_pct: float = 0.10
    max_correlated_exposure_pct: float = 0.15
    min_edge: float = 0.03
    kelly_fraction: float = 0.5
    bootstrap_trades: int = 50
    bootstrap_size_pct: float = 0.01
    cooldown_seconds: int = 300


@dataclass
class ExecutionConfig:
    default_order_type: str = "limit"
    max_slippage: float = 0.01
    max_retries: int = 3


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    approval_timeout: int = 300


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class NotificationsConfig:
    telegram: TelegramConfig = None
    discord: DiscordConfig = None

    def __post_init__(self):
        self.telegram = self.telegram or TelegramConfig()
        self.discord = self.discord or DiscordConfig()


@dataclass
class ConfidenceThresholds:
    auto_execute: float = 0.8
    notify: float = 0.5


@dataclass
class ArbitrageConfig:
    poll_interval: int = 30
    min_spread: float = 0.05


@dataclass
class BotConfig:
    polymarket: PolymarketConfig = None
    signals: SignalsConfig = None
    risk: RiskConfig = None
    execution: ExecutionConfig = None
    notifications: NotificationsConfig = None
    confidence_thresholds: ConfidenceThresholds = None
    arbitrage: ArbitrageConfig = None

    def __post_init__(self):
        self.polymarket = self.polymarket or PolymarketConfig()
        self.signals = self.signals or SignalsConfig()
        self.risk = self.risk or RiskConfig()
        self.execution = self.execution or ExecutionConfig()
        self.notifications = self.notifications or NotificationsConfig()
        self.confidence_thresholds = self.confidence_thresholds or ConfidenceThresholds()
        self.arbitrage = self.arbitrage or ArbitrageConfig()


def _dict_to_dataclass(cls, data: dict):
    if data is None:
        return cls()
    field_names = {f.name for f in fields(cls)}
    filtered = {}
    for k, v in data.items():
        if k in field_names:
            f = next(f for f in fields(cls) if f.name == k)
            if hasattr(f.type, '__dataclass_fields__') or (isinstance(f.type, type) and hasattr(f.type, '__dataclass_fields__')):
                filtered[k] = _dict_to_dataclass(f.type, v) if isinstance(v, dict) else v
            else:
                filtered[k] = v
    return cls(**filtered)


_ENV_MAP = {
    "POLYMARKET_API_KEY": ("polymarket", "api_key"),
    "POLYMARKET_API_SECRET": ("polymarket", "api_secret"),
    "POLYMARKET_PRIVATE_KEY": ("polymarket", "private_key"),
    "NEWSAPI_KEY": ("signals.news", "newsapi_key"),
    "REDDIT_CLIENT_ID": ("signals.social", "reddit_client_id"),
    "REDDIT_CLIENT_SECRET": ("signals.social", "reddit_client_secret"),
    "ANTHROPIC_API_KEY": ("signals.llm", "anthropic_api_key"),
    "ODDS_API_KEY": ("signals.bookmaker", "odds_api_key"),
    "TELEGRAM_BOT_TOKEN": ("notifications.telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("notifications.telegram", "chat_id"),
    "DISCORD_WEBHOOK_URL": ("notifications.discord", "webhook_url"),
}


def _apply_env_overrides(config: BotConfig) -> None:
    for env_var, (path, attr) in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        obj = config
        for part in path.split("."):
            obj = getattr(obj, part)
        setattr(obj, attr, value)


def load_config(path: Path) -> BotConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)

    config = _dict_to_dataclass(BotConfig, raw)
    _apply_env_overrides(config)
    return config
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/config.py tests/test_config.py
git commit -m "feat: YAML config loading with env var overrides"
```

---

## Task 5: Database Layer

**Files:**
- Create: `polymarket_bot/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write failing tests**

`tests/test_database.py`:
```python
import pytest
from datetime import datetime, timezone
from polymarket_bot.database import Database
from polymarket_bot.models import (
    Signal, Direction, TradeExecution, OrderStatus, Market,
)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.initialize()
    yield database
    await database.close()


async def test_initialize_creates_tables(db):
    tables = await db.get_tables()
    assert "trades" in tables
    assert "signals" in tables
    assert "markets" in tables
    assert "portfolio" in tables
    assert "prices" in tables
    assert "orders" in tables


async def test_save_and_get_signal(db):
    signal = Signal(
        source="news", market_id="m1", direction=Direction.YES,
        confidence=0.75, reasoning="test", timestamp=datetime.now(timezone.utc),
    )
    await db.save_signal(signal)
    signals = await db.get_signals("m1")
    assert len(signals) == 1
    assert signals[0]["source"] == "news"
    assert signals[0]["confidence"] == 0.75


async def test_save_and_get_trade(db):
    trade = TradeExecution(
        market_id="m1", direction=Direction.YES, amount=100.0,
        price=0.55, order_id="ord1", status=OrderStatus.FILLED,
    )
    await db.save_trade(trade)
    trades = await db.get_trades()
    assert len(trades) == 1
    assert trades[0]["market_id"] == "m1"


async def test_get_daily_pnl_empty(db):
    pnl = await db.get_daily_pnl()
    assert pnl == 0.0


async def test_get_total_exposure_empty(db):
    exposure = await db.get_total_exposure()
    assert exposure == 0.0


async def test_get_trade_count(db):
    count = await db.get_trade_count()
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_database.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement database**

`polymarket_bot/database.py`:
```python
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from polymarket_bot.models import Signal, TradeExecution

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    order_id TEXT,
    status TEXT NOT NULL,
    fees REAL DEFAULT 0.0,
    realized_pnl REAL DEFAULT 0.0,
    error TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    reasoning TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    end_date TEXT,
    tokens TEXT,
    current_price REAL,
    category TEXT DEFAULT '',
    correlation_tags TEXT DEFAULT '[]',
    platform_mappings TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS portfolio (
    market_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    market_id TEXT NOT NULL,
    price REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def _write(self, sql: str, params: tuple = ()) -> None:
        async with self._write_lock:
            await self._db.execute(sql, params)
            await self._db.commit()

    async def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self._db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_tables(self) -> list[str]:
        rows = await self._fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [r["name"] for r in rows]

    async def save_signal(self, signal: Signal) -> None:
        await self._write(
            "INSERT INTO signals (source, market_id, direction, confidence, reasoning, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal.source, signal.market_id, signal.direction.value,
             signal.confidence, signal.reasoning, signal.timestamp.isoformat()),
        )

    async def get_signals(self, market_id: str, since_minutes: int = 60) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=since_minutes)).isoformat()
        return await self._fetch_all(
            "SELECT * FROM signals WHERE market_id = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (market_id, since),
        )

    async def save_trade(self, trade: TradeExecution) -> None:
        await self._write(
            "INSERT INTO trades (market_id, direction, amount, price, order_id, status, fees, realized_pnl, error, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade.market_id, trade.direction.value, trade.amount, trade.price,
             trade.order_id, trade.status.value, trade.fees, trade.realized_pnl,
             trade.error, trade.timestamp.isoformat()),
        )

    async def get_trades(self, market_id: str | None = None) -> list[dict]:
        if market_id:
            return await self._fetch_all(
                "SELECT * FROM trades WHERE market_id = ? ORDER BY timestamp DESC", (market_id,)
            )
        return await self._fetch_all("SELECT * FROM trades ORDER BY timestamp DESC")

    async def get_daily_pnl(self) -> float:
        """Sum realized P&L for today from the explicit realized_pnl column.

        realized_pnl is set to 0 for entry trades and calculated at exit/settlement:
        - For a winning YES: realized_pnl = amount * (1.0 - entry_price) - fees
        - For a losing YES: realized_pnl = -amount * entry_price
        - For a winning NO: realized_pnl = amount * entry_price - fees
        - For a losing NO: realized_pnl = -amount * (1.0 - entry_price)
        """
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(realized_pnl), 0) as pnl "
            "FROM trades WHERE timestamp >= ?", (today,)
        )
        return row["pnl"] if row else 0.0

    async def get_total_exposure(self) -> float:
        row = await self._fetch_one(
            "SELECT COALESCE(SUM(amount), 0) as total FROM portfolio"
        )
        return row["total"] if row else 0.0

    async def get_trade_count(self) -> int:
        row = await self._fetch_one("SELECT COUNT(*) as cnt FROM trades")
        return row["cnt"] if row else 0

    async def save_price(self, platform: str, market_id: str, price: float) -> None:
        await self._write(
            "INSERT INTO prices (platform, market_id, price, timestamp) VALUES (?, ?, ?, ?)",
            (platform, market_id, price, datetime.now(timezone.utc).isoformat()),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_database.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/database.py tests/test_database.py
git commit -m "feat: SQLite database layer with single-writer async pattern"
```

---

## Task 6: Rich CLI Interface

**Files:**
- Create: `polymarket_bot/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:
```python
import pytest
from unittest.mock import patch
from io import StringIO
from polymarket_bot.cli import (
    print_banner, format_price, format_pnl, get_log_handler, COLOR_SCHEME,
)


def test_color_scheme_has_required_keys():
    assert "profit" in COLOR_SCHEME
    assert "loss" in COLOR_SCHEME
    assert "signal" in COLOR_SCHEME
    assert "arb" in COLOR_SCHEME
    assert "warning" in COLOR_SCHEME


def test_format_price():
    assert format_price(0.55) == "[bold white]$0.55[/]"
    assert format_price(0.0) == "[bold white]$0.00[/]"


def test_format_pnl_positive():
    result = format_pnl(25.50)
    assert "green" in result
    assert "+$25.50" in result


def test_format_pnl_negative():
    result = format_pnl(-10.00)
    assert "red" in result
    assert "-$10.00" in result


def test_format_pnl_zero():
    result = format_pnl(0.0)
    assert "$0.00" in result


def test_print_banner(capsys):
    print_banner("0.1.0")
    # Should not raise and should produce output
    captured = capsys.readouterr()
    # Rich outputs to stderr or uses console, so just verify no exception


def test_get_log_handler():
    handler = get_log_handler()
    assert handler is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cli.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement CLI**

`polymarket_bot/cli.py`:
```python
import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout

console = Console()

COLOR_SCHEME = {
    "profit": "bold green",
    "loss": "bold red",
    "warning": "bold yellow",
    "signal": "bold cyan",
    "arb": "bold magenta",
    "info": "bold blue",
    "muted": "dim white",
}

BANNER = r"""
[bold cyan]  ____       _       __  __            _        _   ____        _   [/]
[bold cyan] |  _ \ ___ | |_   _|  \/  | __ _ _ __| | _____| |_| __ )  ___ | |_ [/]
[bold cyan] | |_) / _ \| | | | | |\/| |/ _` | '__| |/ / _ \ __|  _ \ / _ \| __|[/]
[bold cyan] |  __/ (_) | | |_| | |  | | (_| | |  |   <  __/ |_| |_) | (_) | |_ [/]
[bold cyan] |_|   \___/|_|\__, |_|  |_|\__,_|_|  |_|\_\___|\__|____/ \___/ \__|[/]
[bold cyan]               |___/                                                 [/]
"""


def print_banner(version: str) -> None:
    console.print(BANNER)
    console.print(
        Panel(
            f"[bold white]v{version}[/] | [cyan]Signal-Based Trading[/] | [magenta]Arbitrage Detection[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


def format_price(price: float) -> str:
    return f"[bold white]${price:.2f}[/]"


def format_pnl(pnl: float) -> str:
    if pnl > 0:
        return f"[{COLOR_SCHEME['profit']}]+${pnl:.2f}[/]"
    elif pnl < 0:
        return f"[{COLOR_SCHEME['loss']}]-${abs(pnl):.2f}[/]"
    return f"[{COLOR_SCHEME['muted']}]$0.00[/]"


def format_confidence(confidence: float) -> str:
    if confidence >= 0.8:
        return f"[{COLOR_SCHEME['profit']}]{confidence:.0%}[/]"
    elif confidence >= 0.5:
        return f"[{COLOR_SCHEME['warning']}]{confidence:.0%}[/]"
    return f"[{COLOR_SCHEME['muted']}]{confidence:.0%}[/]"


def format_signal_source(source: str) -> str:
    return f"[{COLOR_SCHEME['signal']}]{source}[/]"


def format_arb(spread: float) -> str:
    return f"[{COLOR_SCHEME['arb']}]{spread:.1%} spread[/]"


def print_trade_execution(market_id: str, direction: str, amount: float, price: float) -> None:
    color = COLOR_SCHEME["profit"] if direction == "YES" else COLOR_SCHEME["loss"]
    console.print(
        Panel(
            f"[{color}]{direction}[/] {format_price(price)} × ${amount:.2f}\n"
            f"[{COLOR_SCHEME['muted']}]Market: {market_id}[/]",
            title="[bold]Trade Executed[/]",
            border_style="green",
        )
    )


def print_signal(source: str, market_id: str, direction: str, confidence: float) -> None:
    console.print(
        f"  [{COLOR_SCHEME['signal']}]SIGNAL[/] "
        f"{format_signal_source(source)} → {direction} "
        f"{format_confidence(confidence)} "
        f"[{COLOR_SCHEME['muted']}]{market_id}[/]"
    )


def print_arb_opportunity(platforms: list[str], spread: float, profit: float) -> None:
    console.print(
        Panel(
            f"{format_arb(spread)} across {', '.join(platforms)}\n"
            f"Est. profit: {format_pnl(profit)}",
            title=f"[{COLOR_SCHEME['arb']}]Arbitrage Opportunity[/]",
            border_style="magenta",
        )
    )


def print_circuit_breaker(daily_loss: float, limit: float) -> None:
    console.print(
        Panel(
            f"[{COLOR_SCHEME['loss']}]Daily loss {format_pnl(daily_loss)} hit limit ${limit:.2f}[/]\n"
            f"[{COLOR_SCHEME['warning']}]ALL TRADING HALTED[/]",
            title=f"[{COLOR_SCHEME['loss']}]CIRCUIT BREAKER[/]",
            border_style="red",
        )
    )


def build_dashboard_table(positions: list[dict], pnl: float, exposure: float, bankroll: float) -> Table:
    table = Table(title="[bold cyan]Portfolio Dashboard[/]", border_style="cyan")
    table.add_column("Market", style="white", max_width=40)
    table.add_column("Side", justify="center")
    table.add_column("Amount", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")

    for pos in positions:
        side_color = COLOR_SCHEME["profit"] if pos.get("direction") == "YES" else COLOR_SCHEME["loss"]
        table.add_row(
            pos.get("market_id", "")[:40],
            f"[{side_color}]{pos.get('direction', '')}[/]",
            f"${pos.get('amount', 0):.2f}",
            format_price(pos.get("entry_price", 0)),
            format_price(pos.get("current_price", 0)),
            format_pnl(pos.get("pnl", 0)),
        )

    table.add_section()
    table.add_row(
        "[bold]Total[/]", "", f"[bold]${exposure:.2f}[/]", "", "",
        format_pnl(pnl),
    )
    return table


def get_log_handler() -> RichHandler:
    return RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cli.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/cli.py tests/test_cli.py
git commit -m "feat: Rich CLI with colorful dashboard, formatting, and banner"
```

---

## Task 7: Signal Plugin Base & News Signal

**Files:**
- Create: `polymarket_bot/signals/__init__.py`
- Create: `polymarket_bot/signals/base.py`
- Create: `polymarket_bot/signals/news.py`
- Create: `tests/test_signals/__init__.py`
- Create: `tests/test_signals/conftest.py`
- Create: `tests/test_signals/test_base.py`
- Create: `tests/test_signals/test_news.py`

- [ ] **Step 1: Write failing tests for base**

`tests/test_signals/test_base.py`:
```python
import pytest
from polymarket_bot.signals.base import SignalPlugin
from polymarket_bot.models import Market, Signal
from datetime import datetime, timezone


async def test_signal_plugin_is_abstract():
    with pytest.raises(TypeError):
        SignalPlugin()


class DummyPlugin(SignalPlugin):
    async def start(self): pass
    async def stop(self): pass
    async def evaluate(self, market):
        return None

    @property
    def name(self) -> str:
        return "dummy"


async def test_concrete_plugin_can_be_instantiated():
    plugin = DummyPlugin()
    assert plugin.name == "dummy"
```

- [ ] **Step 2: Write failing tests for news signal**

`tests/test_signals/test_news.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.signals.news import NewsSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Bitcoin reach $100k by end of 2026?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.45,
    )


@pytest.fixture
def news_signal():
    return NewsSignal(api_key="test-key", poll_interval=300)


async def test_news_signal_name(news_signal):
    assert news_signal.name == "news"


async def test_evaluate_returns_none_when_no_articles(news_signal, market):
    with patch.object(news_signal, "_fetch_articles", new_callable=AsyncMock, return_value=[]):
        result = await news_signal.evaluate(market)
        assert result is None


async def test_evaluate_returns_signal_with_articles(news_signal, market):
    articles = [
        {"title": "Bitcoin surges past $90k", "description": "Major rally continues"},
    ]
    with patch.object(news_signal, "_fetch_articles", new_callable=AsyncMock, return_value=articles):
        with patch.object(news_signal, "_analyze_sentiment", new_callable=AsyncMock,
                         return_value=(Direction.YES, 0.75, "Bullish news")):
            result = await news_signal.evaluate(market)
            assert result is not None
            assert result.source == "news"
            assert result.confidence == 0.75
            assert result.direction == Direction.YES
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_signals/ -v
```
Expected: FAIL

- [ ] **Step 4: Implement signal base**

`polymarket_bot/signals/__init__.py`:
```python
```

`polymarket_bot/signals/base.py`:
```python
from abc import ABC, abstractmethod
from polymarket_bot.models import Market, Signal


class SignalPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def evaluate(self, market: Market) -> Signal | None: ...
```

- [ ] **Step 5: Implement news signal**

`polymarket_bot/signals/news.py`:
```python
import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class NewsSignal(SignalPlugin):
    def __init__(self, api_key: str, poll_interval: int = 300):
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "news"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        articles = await self._fetch_articles(market.question)
        if not articles:
            return None

        direction, confidence, reasoning = await self._analyze_sentiment(articles, market)
        if confidence < 0.1:
            return None

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_articles(self, query: str) -> list[dict]:
        if not self._client:
            return []
        keywords = self._extract_keywords(query)
        try:
            resp = await self._client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": keywords,
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("articles", [])
        except Exception:
            logger.exception("Failed to fetch news articles")
            return []

    def _extract_keywords(self, question: str) -> str:
        stop_words = {"will", "the", "a", "an", "by", "in", "of", "to", "is", "be", "at", "on"}
        words = question.replace("?", "").split()
        keywords = [w for w in words if w.lower() not in stop_words]
        return " ".join(keywords[:5])

    async def _analyze_sentiment(
        self, articles: list[dict], market: Market
    ) -> tuple[Direction, float, str]:
        positive = 0
        negative = 0
        titles = []

        for article in articles:
            title = (article.get("title") or "").lower()
            desc = (article.get("description") or "").lower()
            text = f"{title} {desc}"
            titles.append(article.get("title", ""))

            positive_words = ["surge", "rise", "gain", "win", "pass", "approve", "success", "rally", "bullish", "up"]
            negative_words = ["fall", "drop", "lose", "fail", "reject", "crash", "bearish", "down", "decline"]

            positive += sum(1 for w in positive_words if w in text)
            negative += sum(1 for w in negative_words if w in text)

        total = positive + negative
        if total == 0:
            return Direction.YES, 0.0, "No clear sentiment"

        if positive > negative:
            ratio = positive / total
            confidence = min(ratio * 0.9, 0.95)
            direction = Direction.YES
        else:
            ratio = negative / total
            confidence = min(ratio * 0.9, 0.95)
            direction = Direction.NO

        reasoning = f"Analyzed {len(articles)} articles. Sentiment: {positive}+ / {negative}-. Headlines: {'; '.join(titles[:3])}"
        return direction, round(confidence, 3), reasoning
```

`tests/test_signals/__init__.py`:
```python
```

`tests/test_signals/conftest.py`:
```python
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_signals/ -v
```
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add polymarket_bot/signals/ tests/test_signals/
git commit -m "feat: signal plugin base class and news signal plugin"
```

---

## Task 8: Social Signal (Reddit)

**Files:**
- Create: `polymarket_bot/signals/social.py`
- Create: `tests/test_signals/test_social.py`

- [ ] **Step 1: Write failing tests**

`tests/test_signals/test_social.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.social import SocialSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Ethereum flip Bitcoin?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.20,
    )


@pytest.fixture
def social_signal():
    return SocialSignal(reddit_client_id="id", reddit_client_secret="secret")


async def test_social_signal_name(social_signal):
    assert social_signal.name == "social"


async def test_evaluate_no_posts(social_signal, market):
    with patch.object(social_signal, "_fetch_reddit_posts", new_callable=AsyncMock, return_value=[]):
        result = await social_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_posts(social_signal, market):
    posts = [
        {"title": "ETH is mooning!", "score": 500, "num_comments": 200},
        {"title": "Ethereum gaining momentum", "score": 300, "num_comments": 100},
    ]
    with patch.object(social_signal, "_fetch_reddit_posts", new_callable=AsyncMock, return_value=posts):
        result = await social_signal.evaluate(market)
        assert result is not None
        assert result.source == "social"
        assert 0.0 <= result.confidence <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_signals/test_social.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement social signal**

`polymarket_bot/signals/social.py`:
```python
import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class SocialSignal(SignalPlugin):
    def __init__(self, reddit_client_id: str, reddit_client_secret: str, poll_interval: int = 600):
        self._reddit_id = reddit_client_id
        self._reddit_secret = reddit_client_secret
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None

    @property
    def name(self) -> str:
        return "social"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)
        await self._authenticate()

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def _authenticate(self) -> None:
        if not self._client:
            return
        try:
            resp = await self._client.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(self._reddit_id, self._reddit_secret),
                headers={"User-Agent": "PolymarketBot/0.1"},
            )
            resp.raise_for_status()
            self._access_token = resp.json().get("access_token")
        except Exception:
            logger.exception("Reddit authentication failed")

    async def evaluate(self, market: Market) -> Signal | None:
        posts = await self._fetch_reddit_posts(market.question)
        if not posts:
            return None

        direction, confidence, reasoning = self._analyze_posts(posts, market)
        if confidence < 0.1:
            return None

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_reddit_posts(self, query: str) -> list[dict]:
        if not self._client or not self._access_token:
            return []
        keywords = " ".join(query.replace("?", "").split()[:5])
        try:
            resp = await self._client.get(
                "https://oauth.reddit.com/search",
                params={"q": keywords, "sort": "relevance", "t": "day", "limit": 25},
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "User-Agent": "PolymarketBot/0.1",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            return [c["data"] for c in children]
        except Exception:
            logger.exception("Failed to fetch Reddit posts")
            return []

    def _analyze_posts(self, posts: list[dict], market: Market) -> tuple[Direction, float, str]:
        total_score = 0
        total_comments = 0
        positive = 0
        negative = 0

        for post in posts:
            title = (post.get("title") or "").lower()
            score = post.get("score", 0)
            comments = post.get("num_comments", 0)
            total_score += score
            total_comments += comments

            positive_words = ["bullish", "moon", "surge", "win", "yes", "gain", "up", "rally", "support"]
            negative_words = ["bearish", "crash", "dump", "lose", "no", "fail", "down", "decline", "reject"]

            if any(w in title for w in positive_words):
                positive += score
            elif any(w in title for w in negative_words):
                negative += score

        total = positive + negative
        if total == 0:
            return Direction.YES, 0.0, "No clear social sentiment"

        if positive >= negative:
            ratio = positive / total
            direction = Direction.YES
        else:
            ratio = negative / total
            direction = Direction.NO

        volume_factor = min(len(posts) / 25, 1.0)
        confidence = min(ratio * 0.8 * volume_factor, 0.90)

        reasoning = (
            f"Reddit: {len(posts)} posts, total score {total_score}, "
            f"{total_comments} comments. Sentiment: {positive}+ / {negative}-"
        )
        return direction, round(confidence, 3), reasoning
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_signals/test_social.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/signals/social.py tests/test_signals/test_social.py
git commit -m "feat: Reddit-based social signal plugin"
```

---

## Task 9: Poll Signal

**Files:**
- Create: `polymarket_bot/signals/polls.py`
- Create: `tests/test_signals/test_polls.py`

- [ ] **Step 1: Write failing tests**

`tests/test_signals/test_polls.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.polls import PollSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will candidate X win the 2026 election?",
        end_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.45,
        category="politics",
    )


@pytest.fixture
def poll_signal():
    return PollSignal(poll_interval=3600)


async def test_poll_signal_name(poll_signal):
    assert poll_signal.name == "polls"


async def test_evaluate_no_data(poll_signal, market):
    with patch.object(poll_signal, "_fetch_poll_data", new_callable=AsyncMock, return_value=None):
        result = await poll_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_data(poll_signal, market):
    poll_data = {"implied_probability": 0.55, "source": "RCP Average"}
    with patch.object(poll_signal, "_fetch_poll_data", new_callable=AsyncMock, return_value=poll_data):
        result = await poll_signal.evaluate(market)
        assert result is not None
        assert result.source == "polls"
        assert result.direction == Direction.YES  # poll says 0.55 > market 0.45
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_signals/test_polls.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement poll signal**

`polymarket_bot/signals/polls.py`:
```python
import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class PollSignal(SignalPlugin):
    def __init__(self, poll_interval: int = 3600):
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "polls"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        poll_data = await self._fetch_poll_data(market)
        if poll_data is None:
            return None

        implied_prob = poll_data["implied_probability"]
        market_price = market.current_price
        edge = implied_prob - market_price

        if abs(edge) < 0.02:
            return None

        direction = Direction.YES if edge > 0 else Direction.NO
        confidence = min(abs(edge) * 2, 0.95)

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=f"Poll implied: {implied_prob:.0%} vs market: {market_price:.0%} "
                      f"(edge: {edge:+.1%}). Source: {poll_data.get('source', 'unknown')}",
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_poll_data(self, market: Market) -> dict | None:
        if not self._client:
            return None
        if market.category not in ("politics", "election", "policy"):
            return None
        try:
            # RealClearPolitics scraping endpoint — implementation depends on
            # market-specific keyword matching to RCP race pages.
            # This is a placeholder for the actual RCP integration.
            logger.debug("Poll fetch not yet connected to live source for: %s", market.question)
            return None
        except Exception:
            logger.exception("Failed to fetch poll data")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_signals/test_polls.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/signals/polls.py tests/test_signals/test_polls.py
git commit -m "feat: poll signal plugin with RCP integration stub"
```

---

## Task 10: LLM Signal

**Files:**
- Create: `polymarket_bot/signals/llm.py`
- Create: `tests/test_signals/test_llm.py`

- [ ] **Step 1: Write failing tests**

`tests/test_signals/test_llm.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.signals.llm import LLMSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will AI pass the Turing test by 2027?",
        end_date=datetime(2027, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.35,
    )


@pytest.fixture
def llm_signal():
    return LLMSignal(api_key="test-key", model="claude-sonnet-4-6-20250514")


async def test_llm_signal_name(llm_signal):
    assert llm_signal.name == "llm"


async def test_evaluate_parses_response(llm_signal, market):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"probability": 0.65, "reasoning": "Strong AI progress"}')]

    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await llm_signal.evaluate(market)
        assert result is not None
        assert result.source == "llm"
        assert result.direction == Direction.YES
        assert result.confidence == 0.65


async def test_evaluate_handles_api_error(llm_signal, market):
    with patch.object(llm_signal, "_client", create=True) as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        result = await llm_signal.evaluate(market)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_signals/test_llm.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement LLM signal**

`polymarket_bot/signals/llm.py`:
```python
import json
import logging
from datetime import datetime, timezone

import anthropic

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a prediction market analyst. Evaluate the following market and estimate the probability of YES.

Market question: {question}
Current market price (YES): {price:.0%}
Market end date: {end_date}

Respond with ONLY valid JSON:
{{"probability": <float 0.0-1.0>, "reasoning": "<brief explanation>"}}
"""


class LLMSignal(SignalPlugin):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6-20250514"):
        self._api_key = api_key
        self._model = model
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def name(self) -> str:
        return "llm"

    async def start(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()

    async def evaluate(self, market: Market) -> Signal | None:
        if not self._client:
            return None

        try:
            prompt = PROMPT_TEMPLATE.format(
                question=market.question,
                price=market.current_price,
                end_date=market.end_date.strftime("%Y-%m-%d"),
            )

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            parsed = json.loads(text)
            probability = float(parsed["probability"])
            reasoning = parsed.get("reasoning", "")

            if not 0.0 <= probability <= 1.0:
                logger.warning("LLM returned invalid probability: %s", probability)
                return None

            edge = probability - market.current_price
            if abs(edge) < 0.02:
                return None

            direction = Direction.YES if edge > 0 else Direction.NO
            confidence = min(abs(edge) * 2, 0.95)

            return Signal(
                source=self.name,
                market_id=market.id,
                direction=direction,
                confidence=round(confidence, 3),
                reasoning=f"LLM estimate: {probability:.0%} vs market: {market.current_price:.0%}. {reasoning}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("LLM signal evaluation failed")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_signals/test_llm.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/signals/llm.py tests/test_signals/test_llm.py
git commit -m "feat: LLM signal plugin using Claude API"
```

---

## Task 11: Bookmaker Signal

**Files:**
- Create: `polymarket_bot/signals/bookmaker.py`
- Create: `tests/test_signals/test_bookmaker.py`

- [ ] **Step 1: Write failing tests**

`tests/test_signals/test_bookmaker.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from polymarket_bot.signals.bookmaker import BookmakerSignal
from polymarket_bot.models import Market, Direction


@pytest.fixture
def market():
    return Market(
        id="m1", question="Will Team A win the championship?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
        category="sports",
    )


@pytest.fixture
def bookmaker_signal():
    return BookmakerSignal(api_key="test-key", poll_interval=60)


async def test_bookmaker_signal_name(bookmaker_signal):
    assert bookmaker_signal.name == "bookmaker"


async def test_evaluate_no_odds(bookmaker_signal, market):
    with patch.object(bookmaker_signal, "_fetch_odds", new_callable=AsyncMock, return_value=None):
        result = await bookmaker_signal.evaluate(market)
        assert result is None


async def test_evaluate_with_odds(bookmaker_signal, market):
    odds_data = {"implied_probability": 0.55, "bookmakers_count": 5}
    with patch.object(bookmaker_signal, "_fetch_odds", new_callable=AsyncMock, return_value=odds_data):
        result = await bookmaker_signal.evaluate(market)
        assert result is not None
        assert result.source == "bookmaker"
        assert result.direction == Direction.YES  # 0.55 > 0.40
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_signals/test_bookmaker.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement bookmaker signal**

`polymarket_bot/signals/bookmaker.py`:
```python
import logging
from datetime import datetime, timezone

import httpx

from polymarket_bot.models import Direction, Market, Signal
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class BookmakerSignal(SignalPlugin):
    def __init__(self, api_key: str, poll_interval: int = 60):
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "bookmaker"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(self, market: Market) -> Signal | None:
        odds_data = await self._fetch_odds(market)
        if odds_data is None:
            return None

        implied_prob = odds_data["implied_probability"]
        market_price = market.current_price
        edge = implied_prob - market_price

        if abs(edge) < 0.02:
            return None

        direction = Direction.YES if edge > 0 else Direction.NO
        confidence = min(abs(edge) * 2, 0.95)

        return Signal(
            source=self.name,
            market_id=market.id,
            direction=direction,
            confidence=round(confidence, 3),
            reasoning=f"Bookmaker implied: {implied_prob:.0%} vs market: {market_price:.0%} "
                      f"(edge: {edge:+.1%}, from {odds_data['bookmakers_count']} bookmakers)",
            timestamp=datetime.now(timezone.utc),
        )

    async def _fetch_odds(self, market: Market) -> dict | None:
        if not self._client:
            return None
        try:
            resp = await self._client.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": self._api_key},
            )
            resp.raise_for_status()
            # Match market question to available sports/events
            # This requires market-specific keyword matching logic
            # Placeholder: return None until event matching is implemented
            logger.debug("Bookmaker odds fetch — event matching not yet implemented for: %s", market.question)
            return None
        except Exception:
            logger.exception("Failed to fetch bookmaker odds")
            return None

    @staticmethod
    def american_to_probability(american_odds: int) -> float:
        if american_odds > 0:
            return 100 / (american_odds + 100)
        return abs(american_odds) / (abs(american_odds) + 100)

    @staticmethod
    def decimal_to_probability(decimal_odds: float) -> float:
        return 1 / decimal_odds if decimal_odds > 0 else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_signals/test_bookmaker.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/signals/bookmaker.py tests/test_signals/test_bookmaker.py
git commit -m "feat: bookmaker signal plugin with odds conversion utilities"
```

---

## Task 12: Risk Manager

**Files:**
- Create: `polymarket_bot/decision/__init__.py`
- Create: `polymarket_bot/decision/risk.py`
- Create: `tests/test_decision/__init__.py`
- Create: `tests/test_decision/test_risk.py`

- [ ] **Step 1: Write failing tests**

`tests/test_decision/test_risk.py`:
```python
import pytest
from unittest.mock import AsyncMock
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.config import RiskConfig
from polymarket_bot.models import Direction, Signal, TradeDecision, OrderType
from datetime import datetime, timezone


@pytest.fixture
def risk_config():
    return RiskConfig(
        max_position_pct=0.05, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        max_correlated_exposure_pct=0.15, min_edge=0.03, kelly_fraction=0.5,
        bootstrap_trades=50, bootstrap_size_pct=0.01, cooldown_seconds=300,
    )


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_total_exposure.return_value = 0.0
    db.get_daily_pnl.return_value = 0.0
    db.get_trade_count.return_value = 0
    return db


@pytest.fixture
def risk_manager(risk_config, mock_db):
    return RiskManager(config=risk_config, database=mock_db, bankroll=5000.0)


async def test_calculate_position_size_bootstrap(risk_manager):
    # Under 50 trades, should use flat 1% sizing
    size = await risk_manager.calculate_position_size(confidence=0.9, market_price=0.50)
    assert size == 50.0  # 1% of 5000


async def test_calculate_position_size_kelly(risk_manager, mock_db):
    mock_db.get_trade_count.return_value = 51  # Past bootstrap
    size = await risk_manager.calculate_position_size(confidence=0.8, market_price=0.40)
    # Kelly: b = 0.6/0.4 = 1.5, f = 0.5 * ((0.8*1.5 - 0.2) / 1.5) = 0.5 * (1.0/1.5) = 0.333
    # size = 0.333 * 5000 = 1666.67, but capped at max_position (5% = 250)
    assert size <= 250.0


async def test_check_risk_passes(risk_manager):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is True


async def test_check_risk_rejects_low_edge(risk_manager):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.52, signals=[], order_type=OrderType.LIMIT,
    )
    # confidence 0.52 vs market 0.50 → edge = 0.02 < min_edge 0.03
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is False
    assert "edge" in reason.lower()


async def test_check_risk_rejects_circuit_breaker(risk_manager, mock_db):
    mock_db.get_daily_pnl.return_value = -600.0  # > 10% of 5000
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "circuit breaker" in reason.lower()


async def test_check_risk_rejects_max_exposure(risk_manager, mock_db):
    mock_db.get_total_exposure.return_value = 2600.0  # > 50% of 5000
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "exposure" in reason.lower()


async def test_half_kelly_formula():
    # Direct formula test: p=0.7, b=2.0 (market at 0.333)
    # full_kelly = (0.7*2.0 - 0.3) / 2.0 = (1.4-0.3)/2 = 0.55
    # half_kelly = 0.275
    from polymarket_bot.decision.risk import half_kelly
    result = half_kelly(p=0.7, market_price=1/3, fraction=0.5)
    assert abs(result - 0.275) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_decision/test_risk.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement risk manager**

`polymarket_bot/decision/__init__.py`:
```python
```

`tests/test_decision/__init__.py`:
```python
```

`polymarket_bot/decision/risk.py`:
```python
import logging
from datetime import datetime, timezone

from polymarket_bot.config import RiskConfig
from polymarket_bot.database import Database
from polymarket_bot.models import TradeDecision

logger = logging.getLogger(__name__)


def half_kelly(p: float, market_price: float, fraction: float = 0.5) -> float:
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1 - market_price) / market_price  # payout odds
    full_kelly = (p * b - (1 - p)) / b
    if full_kelly <= 0:
        return 0.0
    return fraction * full_kelly


class RiskManager:
    def __init__(self, config: RiskConfig, database: Database, bankroll: float):
        self._config = config
        self._db = database
        self._bankroll = bankroll
        self._circuit_breaker_active = False
        self._cooldowns: dict[str, datetime] = {}

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_breaker_active

    async def calculate_position_size(self, confidence: float, market_price: float) -> float:
        trade_count = await self._db.get_trade_count()

        if trade_count < self._config.bootstrap_trades:
            size = self._bankroll * self._config.bootstrap_size_pct
        else:
            fraction = half_kelly(confidence, market_price, self._config.kelly_fraction)
            size = self._bankroll * fraction

        max_position = self._bankroll * self._config.max_position_pct
        return min(size, max_position)

    async def check(self, decision: TradeDecision, market_price: float) -> tuple[bool, str]:
        # Circuit breaker
        daily_pnl = await self._db.get_daily_pnl()
        max_loss = self._bankroll * self._config.max_daily_loss_pct
        if daily_pnl < -max_loss:
            self._circuit_breaker_active = True
            return False, f"Circuit breaker: daily loss ${abs(daily_pnl):.2f} exceeds limit ${max_loss:.2f}"

        # Max total exposure
        exposure = await self._db.get_total_exposure()
        max_exposure = self._bankroll * self._config.max_exposure_pct
        if exposure + decision.amount > max_exposure:
            return False, f"Max exposure: current ${exposure:.2f} + ${decision.amount:.2f} exceeds ${max_exposure:.2f}"

        # Max position per market
        max_position = self._bankroll * self._config.max_position_pct
        if decision.amount > max_position:
            return False, f"Max position: ${decision.amount:.2f} exceeds ${max_position:.2f}"

        # Min edge
        if decision.direction.value == "YES":
            edge = decision.confidence - market_price
        else:
            edge = (1 - decision.confidence) - (1 - market_price)
            edge = market_price - decision.confidence  # simplified

        if abs(decision.confidence - market_price) < self._config.min_edge:
            return False, f"Insufficient edge: {abs(decision.confidence - market_price):.1%} < {self._config.min_edge:.1%}"

        # Cooldown
        last_exit = self._cooldowns.get(decision.market_id)
        if last_exit:
            elapsed = (datetime.now(timezone.utc) - last_exit).total_seconds()
            if elapsed < self._config.cooldown_seconds:
                remaining = self._config.cooldown_seconds - elapsed
                return False, f"Cooldown: {remaining:.0f}s remaining for market {decision.market_id}"

        return True, "Approved"

    def record_exit(self, market_id: str) -> None:
        self._cooldowns[market_id] = datetime.now(timezone.utc)

    def update_bankroll(self, new_bankroll: float) -> None:
        self._bankroll = new_bankroll
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_decision/test_risk.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/decision/ tests/test_decision/
git commit -m "feat: risk manager with Half-Kelly sizing, circuit breaker, and position limits"
```

---

## Task 13: Decision Engine

**Files:**
- Create: `polymarket_bot/decision/engine.py`
- Create: `tests/test_decision/test_engine.py`

- [ ] **Step 1: Write failing tests**

`tests/test_decision/test_engine.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from polymarket_bot.decision.engine import DecisionEngine
from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.models import Signal, Direction, Market, SignalEvent, OrderType


@pytest.fixture
def market():
    return Market(
        id="m1", question="Test?", end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
    )


@pytest.fixture
def mock_risk():
    risk = AsyncMock()
    risk.check.return_value = (True, "Approved")
    risk.calculate_position_size.return_value = 100.0
    risk.circuit_breaker_active = False
    return risk


@pytest.fixture
def mock_bus():
    bus = AsyncMock()
    return bus


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_signals.return_value = []
    return db


@pytest.fixture
def engine(mock_risk, mock_bus, mock_db):
    thresholds = ConfidenceThresholds(auto_execute=0.8, notify=0.5)
    signals_config = SignalsConfig()
    return DecisionEngine(
        risk_manager=mock_risk, event_bus=mock_bus, database=mock_db,
        thresholds=thresholds, signals_config=signals_config,
    )


def test_aggregate_signals_weighted(engine):
    signals = [
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.7, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite = engine.aggregate_signals(signals)
    assert 0.0 < composite < 1.0


def test_aggregate_signals_empty(engine):
    composite = engine.aggregate_signals([])
    assert composite == 0.0


def test_aggregate_conflicting_signals(engine):
    signals = [
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="llm", market_id="m1", direction=Direction.NO,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite = engine.aggregate_signals(signals)
    # Conflicting signals with equal weights and equal confidence should produce ~0.5
    # (neither direction dominates). Allow small tolerance for weight normalization.
    assert 0.4 < composite < 0.6


async def test_determine_action_high_confidence(engine):
    action = engine.determine_action(0.85)
    assert action == "auto_execute"


async def test_determine_action_medium_confidence(engine):
    action = engine.determine_action(0.65)
    assert action == "notify"


async def test_determine_action_low_confidence(engine):
    action = engine.determine_action(0.3)
    assert action == "log_only"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_decision/test_engine.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement decision engine**

`polymarket_bot/decision/engine.py`:
```python
import logging
from datetime import datetime, timezone

from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.database import Database
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import (
    ArbitrageOpportunity, Direction, Market, OrderType, Signal,
    SignalEvent, TradeDecision,
)

logger = logging.getLogger(__name__)

SOURCE_WEIGHT_MAP = {
    "news": "news",
    "social": "social",
    "polls": "polls",
    "llm": "llm",
    "bookmaker": "bookmaker",
}


class DecisionEngine:
    def __init__(
        self,
        risk_manager: RiskManager,
        event_bus: EventBus,
        database: Database,
        thresholds: ConfidenceThresholds,
        signals_config: SignalsConfig,
    ):
        self._risk = risk_manager
        self._bus = event_bus
        self._db = database
        self._thresholds = thresholds
        self._weights = {
            "news": signals_config.news.weight,
            "social": signals_config.social.weight,
            "polls": signals_config.polls.weight,
            "llm": signals_config.llm.weight,
            "bookmaker": signals_config.bookmaker.weight,
        }

    def aggregate_signals(self, signals: list[Signal]) -> float:
        if not signals:
            return 0.0

        yes_score = 0.0
        no_score = 0.0
        total_weight = 0.0

        for signal in signals:
            weight = self._weights.get(signal.source, 0.1)
            total_weight += weight
            if signal.direction == Direction.YES:
                yes_score += weight * signal.confidence
            else:
                no_score += weight * signal.confidence

        if total_weight == 0:
            return 0.0

        yes_composite = yes_score / total_weight
        no_composite = no_score / total_weight

        # Net confidence toward the dominant direction
        if yes_composite >= no_composite:
            return yes_composite
        return 1.0 - no_composite  # Inverted: strong NO = low composite

    def determine_majority_direction(self, signals: list[Signal]) -> Direction:
        yes_weight = 0.0
        no_weight = 0.0
        for signal in signals:
            w = self._weights.get(signal.source, 0.1)
            if signal.direction == Direction.YES:
                yes_weight += w * signal.confidence
            else:
                no_weight += w * signal.confidence
        return Direction.YES if yes_weight >= no_weight else Direction.NO

    def determine_action(self, composite_confidence: float) -> str:
        if composite_confidence >= self._thresholds.auto_execute:
            return "auto_execute"
        elif composite_confidence >= self._thresholds.notify:
            return "notify"
        return "log_only"

    async def on_signal(self, signal_event: SignalEvent) -> None:
        if self._risk.circuit_breaker_active:
            logger.warning("Circuit breaker active — ignoring signal")
            return

        signal = signal_event.signal
        market = signal_event.market
        await self._db.save_signal(signal)

        recent_rows = await self._db.get_signals(market.id)
        recent_signals = [signal]
        for row in recent_rows:
            try:
                recent_signals.append(Signal(
                    source=row["source"],
                    market_id=row["market_id"],
                    direction=Direction(row["direction"]),
                    confidence=row["confidence"],
                    reasoning=row.get("reasoning", ""),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                ))
            except (KeyError, ValueError):
                continue
        # Deduplicate by source — keep most recent per source
        seen_sources: dict[str, Signal] = {}
        for s in recent_signals:
            if s.source not in seen_sources or s.timestamp > seen_sources[s.source].timestamp:
                seen_sources[s.source] = s
        recent_signals = list(seen_sources.values())

        composite = self.aggregate_signals(recent_signals)
        action = self.determine_action(composite)

        if action == "log_only":
            logger.info("Low confidence %.2f for %s — logging only", composite, market.id)
            return

        direction = self.determine_majority_direction(recent_signals)
        size = await self._risk.calculate_position_size(composite, market.current_price)

        decision = TradeDecision(
            market_id=market.id,
            direction=direction,
            amount=size,
            confidence=composite,
            signals=recent_signals,
            order_type=OrderType.LIMIT,
        )

        approved, reason = await self._risk.check(decision, market.current_price)
        if not approved:
            logger.info("Risk rejected: %s", reason)
            return

        if action == "auto_execute":
            await self._bus.publish("trade_decision", decision)
        elif action == "notify":
            await self._bus.publish("approval_request", decision)

    async def on_arb_opportunity(self, arb: ArbitrageOpportunity) -> None:
        if self._risk.circuit_breaker_active:
            return

        polymarket_id = arb.market_ids.get("polymarket")
        if not polymarket_id:
            return

        polymarket_price = arb.prices.get("polymarket", 0)
        avg_other = sum(
            p for k, p in arb.prices.items() if k != "polymarket"
        ) / max(len(arb.prices) - 1, 1)

        direction = Direction.YES if avg_other > polymarket_price else Direction.NO
        size = await self._risk.calculate_position_size(arb.confidence, polymarket_price)

        decision = TradeDecision(
            market_id=polymarket_id,
            direction=direction,
            amount=size,
            confidence=arb.confidence,
            signals=[],
            order_type=OrderType.MARKET if arb.time_sensitivity == "high" else OrderType.LIMIT,
            arb_opportunity=arb,
        )

        approved, reason = await self._risk.check(decision, polymarket_price)
        if not approved:
            logger.info("Arb risk rejected: %s", reason)
            return

        await self._bus.publish("trade_decision", decision)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_decision/test_engine.py -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/decision/engine.py tests/test_decision/test_engine.py
git commit -m "feat: decision engine with signal aggregation and confidence tiers"
```

---

## Task 14: Arbitrage Engine

**Files:**
- Create: `polymarket_bot/arbitrage/__init__.py`
- Create: `polymarket_bot/arbitrage/mapper.py`
- Create: `polymarket_bot/arbitrage/monitor.py`
- Create: `polymarket_bot/arbitrage/detector.py`
- Create: `tests/test_arbitrage/test_mapper.py`
- Create: `tests/test_arbitrage/test_detector.py`

- [ ] **Step 1: Write failing tests for mapper**

`tests/test_arbitrage/test_mapper.py`:
```python
import pytest
from polymarket_bot.arbitrage.mapper import MarketMapper


@pytest.fixture
def mapper():
    mappings = {
        "poly_m1": {"kalshi": "kalshi_m1", "manifold": "mani_m1"},
        "poly_m2": {"kalshi": "kalshi_m2"},
    }
    return MarketMapper(mappings)


def test_get_mappings(mapper):
    result = mapper.get_mappings("poly_m1")
    assert result["kalshi"] == "kalshi_m1"
    assert result["manifold"] == "mani_m1"


def test_get_mappings_unknown(mapper):
    result = mapper.get_mappings("unknown")
    assert result == {}


def test_all_polymarket_ids(mapper):
    ids = mapper.all_polymarket_ids()
    assert "poly_m1" in ids
    assert "poly_m2" in ids


def test_add_mapping(mapper):
    mapper.add_mapping("poly_m3", "kalshi", "kalshi_m3")
    result = mapper.get_mappings("poly_m3")
    assert result["kalshi"] == "kalshi_m3"
```

- [ ] **Step 2: Write failing tests for detector**

`tests/test_arbitrage/test_detector.py`:
```python
import pytest
from polymarket_bot.arbitrage.detector import OpportunityDetector


@pytest.fixture
def detector():
    return OpportunityDetector(min_spread=0.05)


def test_detect_opportunity(detector):
    prices = {"polymarket": 0.40, "kalshi": 0.50}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1", "kalshi": "k1"},
    )
    assert result is not None
    assert result.spread == pytest.approx(0.10, abs=0.01)


def test_no_opportunity_small_spread(detector):
    prices = {"polymarket": 0.50, "kalshi": 0.52}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1", "kalshi": "k1"},
    )
    assert result is None


def test_detect_bookmaker_divergence(detector):
    prices = {"polymarket": 0.40, "bookmaker": 0.52}
    result = detector.check(
        polymarket_id="m1", platform_prices=prices, market_ids={"polymarket": "m1"},
    )
    assert result is not None
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_arbitrage/ -v
```
Expected: FAIL

- [ ] **Step 4: Implement mapper**

`polymarket_bot/arbitrage/__init__.py`:
```python
```

`tests/test_arbitrage/__init__.py`:
```python
```

`polymarket_bot/arbitrage/mapper.py`:
```python
import logging

logger = logging.getLogger(__name__)


class MarketMapper:
    def __init__(self, mappings: dict[str, dict[str, str]] | None = None):
        self._mappings: dict[str, dict[str, str]] = mappings or {}

    def get_mappings(self, polymarket_id: str) -> dict[str, str]:
        return self._mappings.get(polymarket_id, {})

    def all_polymarket_ids(self) -> list[str]:
        return list(self._mappings.keys())

    def add_mapping(self, polymarket_id: str, platform: str, platform_id: str) -> None:
        if polymarket_id not in self._mappings:
            self._mappings[polymarket_id] = {}
        self._mappings[polymarket_id][platform] = platform_id
        logger.info("Added mapping: %s → %s:%s", polymarket_id, platform, platform_id)

    def remove_mapping(self, polymarket_id: str, platform: str | None = None) -> None:
        if platform:
            self._mappings.get(polymarket_id, {}).pop(platform, None)
        else:
            self._mappings.pop(polymarket_id, None)
```

- [ ] **Step 5: Implement detector**

`polymarket_bot/arbitrage/detector.py`:
```python
import logging
from polymarket_bot.models import ArbitrageOpportunity

logger = logging.getLogger(__name__)


class OpportunityDetector:
    def __init__(self, min_spread: float = 0.05):
        self._min_spread = min_spread

    def check(
        self,
        polymarket_id: str,
        platform_prices: dict[str, float],
        market_ids: dict[str, str],
    ) -> ArbitrageOpportunity | None:
        poly_price = platform_prices.get("polymarket")
        if poly_price is None:
            return None

        other_prices = {k: v for k, v in platform_prices.items() if k != "polymarket"}
        if not other_prices:
            return None

        avg_other = sum(other_prices.values()) / len(other_prices)
        spread = abs(avg_other - poly_price)

        if spread < self._min_spread:
            return None

        time_sensitivity = "high" if spread > 0.15 else "medium"
        estimated_profit = spread * 100  # rough estimate per $100 position

        return ArbitrageOpportunity(
            market_ids=market_ids,
            platforms=list(platform_prices.keys()),
            prices=platform_prices,
            spread=round(spread, 4),
            estimated_profit=round(estimated_profit, 2),
            confidence=min(spread * 5, 0.95),
            time_sensitivity=time_sensitivity,
        )
```

- [ ] **Step 6: Implement price monitor stub**

`polymarket_bot/arbitrage/monitor.py`:
```python
import asyncio
import json
import logging

import httpx
import websockets

from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus

logger = logging.getLogger(__name__)

POLYMARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PriceMonitor:
    def __init__(
        self,
        mapper: MarketMapper,
        detector: OpportunityDetector,
        event_bus: EventBus,
        database: Database,
        poll_interval: int = 30,
    ):
        self._mapper = mapper
        self._detector = detector
        self._bus = event_bus
        self._db = database
        self._poll_interval = poll_interval
        self._running = False
        self._prices: dict[str, dict[str, float]] = {}  # market_id -> {platform: price}
        self._price_timestamps: dict[str, dict[str, float]] = {}  # market_id -> {platform: epoch}
        self._http_client: httpx.AsyncClient | None = None
        self._ws_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._max_price_age = poll_interval * 3  # Reject prices older than 3x poll interval

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30)
        self._ws_task = asyncio.create_task(self._subscribe_polymarket())
        self._poll_task = asyncio.create_task(self._poll_external_platforms())

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        if self._http_client:
            await self._http_client.aclose()

    async def _subscribe_polymarket(self) -> None:
        market_ids = self._mapper.all_polymarket_ids()
        if not market_ids:
            logger.info("No market mappings configured — skipping Polymarket WS")
            return

        while self._running:
            try:
                async with websockets.connect(POLYMARKET_WS) as ws:
                    for mid in market_ids:
                        await ws.send(json.dumps({
                            "type": "subscribe",
                            "market": mid,
                        }))

                    async for message in ws:
                        if not self._running:
                            break
                        data = json.loads(message)
                        market_id = data.get("market")
                        price = data.get("price")
                        if market_id and price is not None:
                            self._update_price("polymarket", market_id, float(price))
            except Exception:
                logger.exception("Polymarket WS connection error — reconnecting in 5s")
                await asyncio.sleep(5)

    async def _poll_external_platforms(self) -> None:
        while self._running:
            for poly_id in self._mapper.all_polymarket_ids():
                mappings = self._mapper.get_mappings(poly_id)
                for platform, platform_id in mappings.items():
                    price = await self._fetch_platform_price(platform, platform_id)
                    if price is not None:
                        self._update_price(platform, poly_id, price)

                # Check for opportunities after updating prices
                if poly_id in self._prices:
                    opp = self._detector.check(
                        polymarket_id=poly_id,
                        platform_prices=self._prices[poly_id],
                        market_ids={"polymarket": poly_id, **self._mapper.get_mappings(poly_id)},
                    )
                    if opp:
                        await self._bus.publish("arb_opportunity", opp)

            await asyncio.sleep(self._poll_interval)

    def get_cached_price(self, platform: str, polymarket_id: str) -> float | None:
        price = self._prices.get(polymarket_id, {}).get(platform)
        if price is None:
            return None
        # Check staleness
        ts = self._price_timestamps.get(polymarket_id, {}).get(platform, 0)
        import time
        if time.time() - ts > self._max_price_age:
            logger.warning("Stale price for %s/%s (age %.0fs)", platform, polymarket_id, time.time() - ts)
            return None
        return price

    def _update_price(self, platform: str, polymarket_id: str, price: float) -> None:
        import time
        if polymarket_id not in self._prices:
            self._prices[polymarket_id] = {}
            self._price_timestamps[polymarket_id] = {}
        self._prices[polymarket_id][platform] = price
        self._price_timestamps[polymarket_id][platform] = time.time()

    async def _fetch_platform_price(self, platform: str, platform_id: str) -> float | None:
        try:
            if platform == "kalshi":
                return await self._fetch_kalshi(platform_id)
            elif platform == "manifold":
                return await self._fetch_manifold(platform_id)
        except Exception:
            logger.exception("Failed to fetch %s price for %s", platform, platform_id)
        return None

    async def _fetch_kalshi(self, market_id: str) -> float | None:
        if not self._http_client:
            return None
        resp = await self._http_client.get(
            f"https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("market", {}).get("last_price")

    async def _fetch_manifold(self, market_id: str) -> float | None:
        if not self._http_client:
            return None
        resp = await self._http_client.get(
            f"https://api.manifold.markets/v0/market/{market_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("probability")
```

- [ ] **Step 7: Write tests for price monitor**

`tests/test_arbitrage/test_monitor.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from polymarket_bot.arbitrage.monitor import PriceMonitor
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.event_bus import EventBus


@pytest.fixture
def monitor():
    mapper = MarketMapper({"poly_m1": {"kalshi": "k1"}})
    detector = OpportunityDetector(min_spread=0.05)
    bus = EventBus()
    db = AsyncMock()
    return PriceMonitor(mapper=mapper, detector=detector, event_bus=bus, database=db)


def test_get_cached_price_none(monitor):
    assert monitor.get_cached_price("polymarket", "unknown") is None


def test_update_and_get_cached_price(monitor):
    monitor._update_price("polymarket", "poly_m1", 0.55)
    assert monitor.get_cached_price("polymarket", "poly_m1") == 0.55


def test_update_multiple_platforms(monitor):
    monitor._update_price("polymarket", "poly_m1", 0.45)
    monitor._update_price("kalshi", "poly_m1", 0.55)
    assert monitor.get_cached_price("polymarket", "poly_m1") == 0.45
    assert monitor.get_cached_price("kalshi", "poly_m1") == 0.55
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
python -m pytest tests/test_arbitrage/ -v
```
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add polymarket_bot/arbitrage/ tests/test_arbitrage/
git commit -m "feat: arbitrage engine with market mapper, price monitor, and opportunity detector"
```

---

## Task 15: Execution Engine

**Files:**
- Create: `polymarket_bot/execution/__init__.py`
- Create: `polymarket_bot/execution/engine.py`
- Create: `tests/test_execution/__init__.py`
- Create: `tests/test_execution/test_engine.py`

- [ ] **Step 1: Write failing tests**

`tests/test_execution/test_engine.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from polymarket_bot.execution.engine import ExecutionEngine
from polymarket_bot.config import ExecutionConfig
from polymarket_bot.models import TradeDecision, Direction, OrderType, OrderStatus


@pytest.fixture
def exec_config():
    return ExecutionConfig(default_order_type="limit", max_slippage=0.01, max_retries=3)


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_bus():
    return AsyncMock()


@pytest.fixture
def engine(exec_config, mock_db, mock_bus):
    return ExecutionEngine(config=exec_config, database=mock_db, event_bus=mock_bus)


def test_check_slippage_ok(engine):
    assert engine.check_slippage(target_price=0.50, actual_price=0.505) is True


def test_check_slippage_too_high(engine):
    assert engine.check_slippage(target_price=0.50, actual_price=0.52) is False


async def test_execute_trade_success(engine, mock_bus, mock_db):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(engine, "_place_order", new_callable=AsyncMock,
                     return_value=("ord123", 0.50, OrderStatus.FILLED)):
        await engine.execute(decision, current_price=0.50)
        mock_bus.publish.assert_called_once()
        mock_db.save_trade.assert_called_once()


async def test_execute_trade_slippage_reject(engine, mock_bus):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.MARKET,
    )
    with patch.object(engine, "_get_best_price", new_callable=AsyncMock, return_value=0.55):
        await engine.execute(decision, current_price=0.50)
        mock_bus.publish.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_execution/ -v
```
Expected: FAIL

- [ ] **Step 3: Implement execution engine**

`polymarket_bot/execution/__init__.py`:
```python
```

`tests/test_execution/__init__.py`:
```python
```

`polymarket_bot/execution/engine.py`:
```python
import asyncio
import logging
import random
from datetime import datetime, timezone

from polymarket_bot.config import ExecutionConfig
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus
from polymarket_bot.models import (
    Direction, OrderStatus, OrderType, TradeDecision, TradeExecution,
)

logger = logging.getLogger(__name__)


class ExecutionEngine:
    def __init__(self, config: ExecutionConfig, database: Database, event_bus: EventBus):
        self._config = config
        self._db = database
        self._bus = event_bus
        self._clob_client = None  # Initialized in start()

    async def start(self, api_key: str, api_secret: str, private_key: str, chain_id: int) -> None:
        try:
            from py_clob_client.client import ClobClient
            self._clob_client = ClobClient(
                host="https://clob.polymarket.com",
                key=api_key,
                chain_id=chain_id,
                funder=private_key,
            )
            logger.info("CLOB client initialized")
        except Exception:
            logger.exception("Failed to initialize CLOB client")

    async def stop(self) -> None:
        self._clob_client = None

    async def get_balance(self) -> float | None:
        """Fetch USDC balance from the connected wallet via CLOB client."""
        if not self._clob_client:
            return None
        try:
            # py-clob-client provides get_balance() or similar
            # The exact method depends on py-clob-client version
            balance = self._clob_client.get_balance_allowance()
            return float(balance.get("balance", 0)) / 1e6  # USDC has 6 decimals
        except Exception:
            logger.exception("Failed to fetch wallet balance")
            return None

    def check_slippage(self, target_price: float, actual_price: float) -> bool:
        slippage = abs(actual_price - target_price) / target_price
        return slippage <= self._config.max_slippage

    async def _get_best_price(self, market_id: str, direction: Direction) -> float | None:
        # Placeholder — in production, query CLOB orderbook
        return None

    async def _place_order(
        self, market_id: str, direction: Direction, amount: float,
        price: float, order_type: OrderType,
    ) -> tuple[str, float, OrderStatus]:
        if not self._clob_client:
            raise RuntimeError("CLOB client not initialized")

        # Placeholder for actual CLOB order placement
        # In production:
        # token_id = market.tokens[direction.value]
        # if order_type == OrderType.LIMIT:
        #     order = self._clob_client.create_and_post_order(...)
        # else:
        #     order = self._clob_client.create_and_post_market_order(...)
        logger.info("Placing %s order: %s %s @ $%.4f × $%.2f",
                    order_type.value, direction.value, market_id, price, amount)
        return "order_placeholder", price, OrderStatus.PLACED

    async def execute(self, decision: TradeDecision, current_price: float) -> None:
        # Slippage check for market orders
        if decision.order_type == OrderType.MARKET:
            best_price = await self._get_best_price(decision.market_id, decision.direction)
            if best_price and not self.check_slippage(current_price, best_price):
                logger.warning(
                    "Slippage too high for %s: target=%.4f actual=%.4f",
                    decision.market_id, current_price, best_price,
                )
                return

        # Retry loop
        last_error = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                order_id, fill_price, status = await self._place_order(
                    decision.market_id, decision.direction, decision.amount,
                    current_price, decision.order_type,
                )

                execution = TradeExecution(
                    market_id=decision.market_id,
                    direction=decision.direction,
                    amount=decision.amount,
                    price=fill_price,
                    order_id=order_id,
                    status=status,
                )

                await self._db.save_trade(execution)
                await self._bus.publish("trade_execution", execution)
                logger.info("Trade executed: %s %s $%.2f @ $%.4f",
                           decision.direction.value, decision.market_id,
                           decision.amount, fill_price)
                return

            except Exception as e:
                last_error = str(e)
                logger.warning("Order attempt %d/%d failed: %s",
                             attempt, self._config.max_retries, e)
                if attempt < self._config.max_retries:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 1))

        # All retries exhausted
        execution = TradeExecution(
            market_id=decision.market_id,
            direction=decision.direction,
            amount=decision.amount,
            price=current_price,
            order_id="",
            status=OrderStatus.FAILED,
            error=last_error,
        )
        await self._db.save_trade(execution)
        logger.error("Trade failed after %d attempts: %s", self._config.max_retries, last_error)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_execution/ -v
```
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/execution/ tests/test_execution/
git commit -m "feat: execution engine with slippage protection and retry logic"
```

---

## Task 16: Notification System

**Files:**
- Create: `polymarket_bot/notifications/__init__.py`
- Create: `polymarket_bot/notifications/base.py`
- Create: `polymarket_bot/notifications/telegram.py`
- Create: `polymarket_bot/notifications/discord.py`
- Create: `tests/test_notifications/__init__.py`
- Create: `tests/test_notifications/test_telegram.py`

- [ ] **Step 1: Write failing tests**

`tests/test_notifications/test_telegram.py`:
```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from polymarket_bot.notifications.telegram import TelegramNotifier
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.models import TradeDecision, Direction, OrderType


@pytest.fixture
def notifier():
    return TelegramNotifier(bot_token="test-token", chat_id="12345", approval_timeout=5)


async def test_notifier_name(notifier):
    assert notifier.name == "telegram"


async def test_send_alert(notifier):
    with patch.object(notifier, "_send_message", new_callable=AsyncMock) as mock_send:
        await notifier.send_alert("Test alert", NotificationLevel.INFO)
        mock_send.assert_called_once()
        call_text = mock_send.call_args[0][0]
        assert "Test alert" in call_text


async def test_send_trade_notification(notifier):
    with patch.object(notifier, "_send_message", new_callable=AsyncMock) as mock_send:
        await notifier.send_trade_notification(
            market_id="m1", direction="YES", amount=100.0, price=0.55,
        )
        mock_send.assert_called_once()


async def test_request_approval_timeout(notifier):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_approval_message", new_callable=AsyncMock):
        with patch.object(notifier, "_wait_for_response", new_callable=AsyncMock, return_value=None):
            result = await notifier.request_approval(decision)
            assert result is False  # Timeout = rejected


async def test_request_approval_approved(notifier):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_approval_message", new_callable=AsyncMock):
        with patch.object(notifier, "_wait_for_response", new_callable=AsyncMock, return_value=True):
            result = await notifier.request_approval(decision)
            assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_notifications/ -v
```
Expected: FAIL

- [ ] **Step 3: Implement notification base**

`polymarket_bot/notifications/__init__.py`:
```python
```

`tests/test_notifications/__init__.py`:
```python
```

`polymarket_bot/notifications/base.py`:
```python
from abc import ABC, abstractmethod
from enum import Enum

from polymarket_bot.models import TradeDecision


class NotificationLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


class Notifier(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_alert(self, message: str, level: NotificationLevel) -> None: ...

    @abstractmethod
    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None: ...

    @abstractmethod
    async def request_approval(self, decision: TradeDecision) -> bool: ...
```

- [ ] **Step 4: Implement Telegram notifier**

`polymarket_bot/notifications/telegram.py`:
```python
import asyncio
import logging

from polymarket_bot.models import TradeDecision
from polymarket_bot.notifications.base import Notifier, NotificationLevel

logger = logging.getLogger(__name__)

LEVEL_EMOJI = {
    NotificationLevel.INFO: "\u2139\ufe0f",
    NotificationLevel.WARNING: "\u26a0\ufe0f",
    NotificationLevel.URGENT: "\U0001f6a8",
}


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, approval_timeout: int = 300):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._approval_timeout = approval_timeout
        self._bot = None
        self._pending_approvals: dict[str, asyncio.Future] = {}

    @property
    def name(self) -> str:
        return "telegram"

    async def start(self) -> None:
        try:
            from telegram import Bot
            self._bot = Bot(token=self._bot_token)
            logger.info("Telegram notifier started")
        except Exception:
            logger.exception("Failed to start Telegram bot")

    async def stop(self) -> None:
        self._bot = None

    async def _send_message(self, text: str, parse_mode: str = "HTML") -> None:
        if not self._bot:
            logger.warning("Telegram bot not initialized — message: %s", text[:100])
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode=parse_mode,
            )
        except Exception:
            logger.exception("Failed to send Telegram message")

    async def send_alert(self, message: str, level: NotificationLevel) -> None:
        emoji = LEVEL_EMOJI.get(level, "")
        text = f"{emoji} <b>{level.value.upper()}</b>\n\n{message}"
        await self._send_message(text)

    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None:
        arrow = "\u2b06\ufe0f" if direction == "YES" else "\u2b07\ufe0f"
        text = (
            f"{arrow} <b>Trade Executed</b>\n\n"
            f"Market: <code>{market_id}</code>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Price: <b>${price:.4f}</b>"
        )
        await self._send_message(text)

    async def _send_approval_message(self, decision: TradeDecision) -> None:
        signal_summary = ", ".join(
            f"{s.source}({s.confidence:.0%})" for s in decision.signals[:5]
        )
        text = (
            f"\U0001f4cb <b>Approval Required</b>\n\n"
            f"Market: <code>{decision.market_id}</code>\n"
            f"Direction: <b>{decision.direction.value}</b>\n"
            f"Amount: <b>${decision.amount:.2f}</b>\n"
            f"Confidence: <b>{decision.confidence:.0%}</b>\n"
            f"Signals: {signal_summary or 'N/A'}\n\n"
            f"Reply YES to approve, NO to reject.\n"
            f"Auto-cancels in {self._approval_timeout}s."
        )
        await self._send_message(text)

    async def _wait_for_response(self, market_id: str) -> bool | None:
        # TODO v2: Wire up telegram.ext.Application with CallbackQueryHandler
        # for inline button approval. Requires running a Telegram updater loop
        # in the background (see app.py). For v1, approvals always time out
        # (auto-cancel), which is the safe default per the spec's timeout design.
        try:
            future = asyncio.get_running_loop().create_future()
            self._pending_approvals[market_id] = future
            result = await asyncio.wait_for(future, timeout=self._approval_timeout)
            return result
        except asyncio.TimeoutError:
            logger.info("Approval timeout for market %s", market_id)
            return None
        finally:
            self._pending_approvals.pop(market_id, None)

    async def request_approval(self, decision: TradeDecision) -> bool:
        await self._send_approval_message(decision)
        response = await self._wait_for_response(decision.market_id)
        if response is None:
            await self.send_alert(
                f"Approval expired for {decision.market_id} — trade cancelled.",
                NotificationLevel.WARNING,
            )
            return False
        return response

    def resolve_approval(self, market_id: str, approved: bool) -> None:
        future = self._pending_approvals.get(market_id)
        if future and not future.done():
            future.set_result(approved)
```

- [ ] **Step 5: Implement Discord notifier**

`polymarket_bot/notifications/discord.py`:
```python
import logging

import httpx

from polymarket_bot.models import TradeDecision
from polymarket_bot.notifications.base import Notifier, NotificationLevel

logger = logging.getLogger(__name__)

LEVEL_COLOR = {
    NotificationLevel.INFO: 3447003,      # Blue
    NotificationLevel.WARNING: 16776960,   # Yellow
    NotificationLevel.URGENT: 15158332,    # Red
}


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self._webhook_url = webhook_url
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "discord"

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=30)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def send_alert(self, message: str, level: NotificationLevel) -> None:
        embed = {
            "title": level.value.upper(),
            "description": message,
            "color": LEVEL_COLOR.get(level, 0),
        }
        await self._send_webhook({"embeds": [embed]})

    async def send_trade_notification(
        self, market_id: str, direction: str, amount: float, price: float,
    ) -> None:
        embed = {
            "title": "Trade Executed",
            "color": 3066993 if direction == "YES" else 15158332,
            "fields": [
                {"name": "Market", "value": market_id, "inline": True},
                {"name": "Direction", "value": direction, "inline": True},
                {"name": "Amount", "value": f"${amount:.2f}", "inline": True},
                {"name": "Price", "value": f"${price:.4f}", "inline": True},
            ],
        }
        await self._send_webhook({"embeds": [embed]})

    async def request_approval(self, decision: TradeDecision) -> bool:
        # Discord doesn't support interactive approval — log only
        await self.send_alert(
            f"Approval needed: {decision.direction.value} {decision.market_id} "
            f"${decision.amount:.2f} (confidence: {decision.confidence:.0%})",
            NotificationLevel.WARNING,
        )
        return False  # Cannot approve via Discord webhook

    async def _send_webhook(self, payload: dict) -> None:
        if not self._client or not self._webhook_url:
            return
        try:
            resp = await self._client.post(self._webhook_url, json=payload)
            resp.raise_for_status()
        except Exception:
            logger.exception("Failed to send Discord webhook")
```

- [ ] **Step 6: Write Discord notifier tests**

`tests/test_notifications/test_discord.py`:
```python
import pytest
from unittest.mock import AsyncMock, patch
from polymarket_bot.notifications.discord import DiscordNotifier
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.models import TradeDecision, Direction, OrderType


@pytest.fixture
def notifier():
    return DiscordNotifier(webhook_url="https://discord.com/api/webhooks/test/token")


async def test_discord_name(notifier):
    assert notifier.name == "discord"


async def test_send_alert(notifier):
    await notifier.start()
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock) as mock_send:
        await notifier.send_alert("Server restarted", NotificationLevel.INFO)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][0]
        assert payload["embeds"][0]["title"] == "INFO"
        assert "Server restarted" in payload["embeds"][0]["description"]
    await notifier.stop()


async def test_send_trade_notification(notifier):
    await notifier.start()
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock) as mock_send:
        await notifier.send_trade_notification("m1", "YES", 100.0, 0.55)
        mock_send.assert_called_once()
        embed = mock_send.call_args[0][0]["embeds"][0]
        assert embed["title"] == "Trade Executed"
        assert embed["color"] == 3066993  # Green for YES
    await notifier.stop()


async def test_request_approval_always_false(notifier):
    """Discord webhooks cannot support interactive approval — always returns False."""
    await notifier.start()
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.65, signals=[], order_type=OrderType.LIMIT,
    )
    with patch.object(notifier, "_send_webhook", new_callable=AsyncMock):
        result = await notifier.request_approval(decision)
        assert result is False
    await notifier.stop()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_notifications/ -v
```
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add polymarket_bot/notifications/ tests/test_notifications/
git commit -m "feat: Telegram and Discord notification system with approval flow"
```

---

## Task 17: Main Orchestrator

**Files:**
- Modify: `polymarket_bot/app.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create shared test fixtures**

`tests/conftest.py`:
```python
import pytest
from pathlib import Path
from polymarket_bot.event_bus import EventBus
from polymarket_bot.database import Database


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.initialize()
    yield db
    await db.close()
```

- [ ] **Step 2: Implement the main orchestrator**

`polymarket_bot/app.py`:
```python
import asyncio
import logging
import sys
from pathlib import Path

from polymarket_bot import __version__
from polymarket_bot.cli import (
    console, get_log_handler, print_banner, print_trade_execution,
    print_signal, print_arb_opportunity, print_circuit_breaker,
)
from polymarket_bot.config import load_config, BotConfig
from polymarket_bot.database import Database
from polymarket_bot.decision.engine import DecisionEngine
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.event_bus import EventBus
from polymarket_bot.execution.engine import ExecutionEngine
from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.arbitrage.monitor import PriceMonitor
from polymarket_bot.models import (
    SignalEvent, TradeDecision, TradeExecution, ArbitrageOpportunity,
)
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.signals.base import SignalPlugin
from polymarket_bot.signals.news import NewsSignal
from polymarket_bot.signals.social import SocialSignal
from polymarket_bot.signals.polls import PollSignal
from polymarket_bot.signals.llm import LLMSignal
from polymarket_bot.signals.bookmaker import BookmakerSignal

logger = logging.getLogger("polymarket_bot")


def setup_logging():
    handler = get_log_handler()
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(message)s",
        datefmt="[%X]",
    )


def build_signal_plugins(config: BotConfig) -> list[SignalPlugin]:
    plugins = []
    sc = config.signals
    if sc.news.enabled:
        plugins.append(NewsSignal(api_key=sc.news.newsapi_key, poll_interval=sc.news.poll_interval))
    if sc.social.enabled:
        plugins.append(SocialSignal(
            reddit_client_id=sc.social.reddit_client_id,
            reddit_client_secret=sc.social.reddit_client_secret,
            poll_interval=sc.social.poll_interval,
        ))
    if sc.polls.enabled:
        plugins.append(PollSignal(poll_interval=sc.polls.poll_interval))
    if sc.llm.enabled:
        plugins.append(LLMSignal(api_key=sc.llm.anthropic_api_key, model=sc.llm.model))
    if sc.bookmaker.enabled:
        plugins.append(BookmakerSignal(
            api_key=sc.bookmaker.odds_api_key, poll_interval=sc.bookmaker.poll_interval,
        ))
    return plugins


async def run_bot(config_path: str = "config.yaml"):
    setup_logging()
    print_banner(__version__)

    # Load config
    config = load_config(Path(config_path))
    console.print(f"[bold green]Config loaded[/] from {config_path}")

    # Initialize core
    db = Database(Path("polymarket_bot.db"))
    await db.initialize()
    console.print("[bold green]Database initialized[/]")

    bus = EventBus()

    # Risk manager — fetch real bankroll from execution engine
    bankroll = await exec_engine.get_balance()
    if bankroll is None or bankroll <= 0:
        console.print("[bold red]ERROR: Could not fetch wallet balance. Exiting.[/]")
        await db.close()
        return
    console.print(f"[bold green]Wallet balance:[/] ${bankroll:.2f}")
    risk_manager = RiskManager(config=config.risk, database=db, bankroll=bankroll)

    # Decision engine
    decision_engine = DecisionEngine(
        risk_manager=risk_manager, event_bus=bus, database=db,
        thresholds=config.confidence_thresholds, signals_config=config.signals,
    )

    # Execution engine
    exec_engine = ExecutionEngine(config=config.execution, database=db, event_bus=bus)
    await exec_engine.start(
        api_key=config.polymarket.api_key,
        api_secret=config.polymarket.api_secret,
        private_key=config.polymarket.private_key,
        chain_id=config.polymarket.chain_id,
    )
    console.print("[bold green]Execution engine ready[/]")

    # Notifications
    notifiers = []
    if config.notifications.telegram.enabled:
        from polymarket_bot.notifications.telegram import TelegramNotifier
        tg = TelegramNotifier(
            bot_token=config.notifications.telegram.bot_token,
            chat_id=config.notifications.telegram.chat_id,
            approval_timeout=config.notifications.telegram.approval_timeout,
        )
        await tg.start()
        notifiers.append(tg)
        console.print("[bold green]Telegram notifier active[/]")

    if config.notifications.discord.enabled:
        from polymarket_bot.notifications.discord import DiscordNotifier
        dc = DiscordNotifier(webhook_url=config.notifications.discord.webhook_url)
        await dc.start()
        notifiers.append(dc)
        console.print("[bold green]Discord notifier active[/]")

    # Wire event handlers
    bus.subscribe("signal", decision_engine.on_signal)
    bus.subscribe("arb_opportunity", decision_engine.on_arb_opportunity)

    async def on_trade_decision(decision: TradeDecision):
        # Fetch live price from the price monitor cache or DB
        current_price = monitor.get_cached_price("polymarket", decision.market_id)
        if current_price is None or current_price <= 0:
            logger.warning("No live price for %s — skipping execution", decision.market_id)
            return
        print_trade_execution(decision.market_id, decision.direction.value,
                             decision.amount, current_price)
        await exec_engine.execute(decision, current_price=current_price)

    async def on_approval_request(decision: TradeDecision):
        for notifier in notifiers:
            approved = await notifier.request_approval(decision)
            if approved:
                # Re-fetch price AFTER approval (may be minutes later)
                fresh_price = monitor.get_cached_price("polymarket", decision.market_id)
                if fresh_price is None or fresh_price <= 0:
                    logger.warning("No live price for %s after approval — skipping", decision.market_id)
                    return
                await exec_engine.execute(decision, current_price=fresh_price)
                return
        logger.info("Trade not approved: %s", decision.market_id)

    async def on_trade_execution(execution: TradeExecution):
        print_trade_execution(execution.market_id, execution.direction.value,
                             execution.amount, execution.price)
        # Refresh bankroll after each trade
        new_balance = await exec_engine.get_balance()
        if new_balance and new_balance > 0:
            risk_manager.update_bankroll(new_balance)
        for notifier in notifiers:
            await notifier.send_trade_notification(
                execution.market_id, execution.direction.value,
                execution.amount, execution.price,
            )

    bus.subscribe("trade_decision", on_trade_decision)
    bus.subscribe("approval_request", on_approval_request)
    bus.subscribe("trade_execution", on_trade_execution)

    # Signal plugins
    plugins = build_signal_plugins(config)
    for plugin in plugins:
        await plugin.start()
        console.print(f"[bold green]Signal plugin started:[/] [cyan]{plugin.name}[/]")

    # Arbitrage engine
    mapper = MarketMapper()  # TODO: Load mappings from config/DB
    detector = OpportunityDetector(min_spread=config.arbitrage.min_spread)
    monitor = PriceMonitor(
        mapper=mapper, detector=detector, event_bus=bus, database=db,
        poll_interval=config.arbitrage.poll_interval,
    )
    await monitor.start()
    console.print("[bold green]Arbitrage monitor started[/]")

    console.print("\n[bold cyan]Bot is running. Press Ctrl+C to stop.[/]\n")

    # Main loop — keep alive
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        console.print("\n[bold yellow]Shutting down...[/]")
        await monitor.stop()
        for plugin in plugins:
            await plugin.stop()
        await exec_engine.stop()
        for notifier in notifiers:
            await notifier.stop()
        await db.close()
        console.print("[bold green]Shutdown complete.[/]")
```

- [ ] **Step 3: Update __main__.py with CLI args**

`polymarket_bot/__main__.py`:
```python
import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    from polymarket_bot.app import run_bot
    try:
        asyncio.run(run_bot(config_path=args.config))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add polymarket_bot/app.py polymarket_bot/__main__.py tests/conftest.py
git commit -m "feat: main orchestrator wiring all modules with Rich CLI startup"
```

---

## Task 18: Integration Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

`tests/test_integration.py`:
```python
import pytest
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from polymarket_bot.config import load_config, BotConfig
from polymarket_bot.database import Database
from polymarket_bot.event_bus import EventBus
from polymarket_bot.decision.engine import DecisionEngine
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.models import (
    Direction, Market, Signal, SignalEvent, TradeDecision, OrderType,
)


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
polymarket:
  api_key: "test"
  api_secret: "test"
  private_key: "0x1"
  chain_id: 137
signals:
  news: {enabled: false, poll_interval: 300, weight: 0.2}
  social: {enabled: false, poll_interval: 600, weight: 0.15}
  polls: {enabled: false, poll_interval: 3600, weight: 0.25}
  llm: {enabled: false, weight: 0.25, model: "claude-sonnet-4-6-20250514"}
  bookmaker: {enabled: false, poll_interval: 60, weight: 0.15}
risk:
  max_position_pct: 0.05
  max_exposure_pct: 0.50
  max_daily_loss_pct: 0.10
  max_correlated_exposure_pct: 0.15
  min_edge: 0.03
  kelly_fraction: 0.5
  bootstrap_trades: 50
  bootstrap_size_pct: 0.01
  cooldown_seconds: 300
execution:
  default_order_type: "limit"
  max_slippage: 0.01
  max_retries: 3
notifications:
  telegram: {enabled: false, bot_token: "", chat_id: "", approval_timeout: 300}
  discord: {enabled: false, webhook_url: ""}
confidence_thresholds:
  auto_execute: 0.8
  notify: 0.5
arbitrage:
  poll_interval: 30
  min_spread: 0.05
""")
    return load_config(config_file)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "integration.db")
    await database.initialize()
    yield database
    await database.close()


async def test_full_signal_to_decision_flow(config, db):
    """Test: signal → decision engine → trade decision emitted."""
    bus = EventBus()
    risk = RiskManager(config=config.risk, database=db, bankroll=5000.0)
    engine = DecisionEngine(
        risk_manager=risk, event_bus=bus, database=db,
        thresholds=config.confidence_thresholds, signals_config=config.signals,
    )

    decisions = []

    async def capture_decision(decision):
        decisions.append(decision)

    bus.subscribe("trade_decision", capture_decision)
    bus.subscribe("signal", engine.on_signal)

    market = Market(
        id="m1", question="Test market?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.30,
    )

    signal = Signal(
        source="news", market_id="m1", direction=Direction.YES,
        confidence=0.9, reasoning="Very strong signal",
        timestamp=datetime.now(timezone.utc),
    )

    event = SignalEvent(signal=signal, market=market)
    await bus.publish("signal", event)
    await asyncio.sleep(0.1)

    assert len(decisions) == 1
    assert decisions[0].market_id == "m1"
    assert decisions[0].direction == Direction.YES


async def test_circuit_breaker_blocks_trades(config, db):
    """Test: circuit breaker prevents trade decisions."""
    bus = EventBus()
    risk = RiskManager(config=config.risk, database=db, bankroll=5000.0)
    risk._circuit_breaker_active = True  # Simulate triggered breaker

    engine = DecisionEngine(
        risk_manager=risk, event_bus=bus, database=db,
        thresholds=config.confidence_thresholds, signals_config=config.signals,
    )

    decisions = []
    bus.subscribe("trade_decision", lambda d: decisions.append(d))
    bus.subscribe("signal", engine.on_signal)

    market = Market(
        id="m1", question="Test?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.30,
    )
    signal = Signal(
        source="news", market_id="m1", direction=Direction.YES,
        confidence=0.95, reasoning="Strong",
        timestamp=datetime.now(timezone.utc),
    )

    await bus.publish("signal", SignalEvent(signal=signal, market=market))
    await asyncio.sleep(0.1)

    assert len(decisions) == 0  # Breaker blocked it
```

- [ ] **Step 2: Run integration test**

```bash
python -m pytest tests/test_integration.py -v
```
Expected: All PASS

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: integration smoke tests for signal-to-decision flow"
```

---

## Task 19: Final Wiring & Verification

- [ ] **Step 1: Run full test suite one more time**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: All tests pass

- [ ] **Step 2: Run linter**

```bash
python -m ruff check polymarket_bot/ tests/
```
Expected: No errors (or only minor style warnings)

- [ ] **Step 3: Verify bot starts (dry run)**

```bash
cp config.example.yaml config.yaml
python -m polymarket_bot -c config.yaml
```
Expected: Banner displays, modules initialize, then errors on API keys (expected since they're placeholders). Ctrl+C exits cleanly.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final wiring and cleanup"
```
