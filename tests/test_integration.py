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
    """Test: signal -> decision engine -> trade decision emitted."""
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
    risk._circuit_breaker_active = True

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

    assert len(decisions) == 0
