import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from polymarket_bot.decision.engine import DecisionEngine, _infer_category
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
    risk._bankroll = 5000.0
    risk._config = MagicMock()
    risk._config.max_exposure_pct = 0.50
    return risk


@pytest.fixture
def mock_bus():
    bus = AsyncMock()
    return bus


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_signals.return_value = []
    db.get_total_exposure.return_value = 0.0
    db.save_signal_outcome = AsyncMock()
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
    # Use polls and bookmaker — equal weight (0.20 each) to get clean 50/50
    signals = [
        Signal(source="polls", market_id="m1", direction=Direction.YES,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="bookmaker", market_id="m1", direction=Direction.NO,
               confidence=0.9, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite = engine.aggregate_signals(signals)
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


@pytest.mark.asyncio
async def test_single_source_downgrades_to_notify(engine, market, mock_db):
    """A single high-confidence signal should NOT auto_execute — downgrade to notify."""
    signal = Signal(
        source="llm", market_id="m1", direction=Direction.YES,
        confidence=0.95, reasoning="very confident", timestamp=datetime.now(timezone.utc),
    )
    event = SignalEvent(signal=signal, market=market)
    await engine.on_signal(event)

    # Should have published approval_request (notify), not trade_decision (auto_execute)
    calls = engine._bus.publish.call_args_list
    if calls:
        topics = [c[0][0] for c in calls]
        assert "trade_decision" not in topics or any(
            c[0][0] == "approval_request" for c in calls
        )


def test_correlation_discount_correlated_sources(engine):
    """Correlated sources (news+llm+social) should get discounted."""
    signals = [
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="social", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite_corr = engine.aggregate_signals(signals)

    # Independent sources should NOT be discounted
    signals_indep = [
        Signal(source="polls", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="favorite_longshot", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="weather", market_id="m1", direction=Direction.YES,
               confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc)),
    ]
    composite_indep = engine.aggregate_signals(signals_indep)

    # Correlated group should produce lower composite than independent group
    assert composite_corr < composite_indep


def test_freshness_decay_structural_vs_news(engine):
    """Structural signals (FLB) should decay much slower than news."""
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)

    flb_signal = Signal(source="favorite_longshot", market_id="m1",
                        direction=Direction.YES, confidence=0.8,
                        reasoning="", timestamp=two_hours_ago)
    news_signal = Signal(source="news", market_id="m1",
                         direction=Direction.YES, confidence=0.8,
                         reasoning="", timestamp=two_hours_ago)

    flb_freshness = engine._freshness_factor(flb_signal)
    news_freshness = engine._freshness_factor(news_signal)

    # FLB (24h half-life) should retain much more weight than news (1h half-life)
    assert flb_freshness > 0.85  # 2h / 1440 half-life ≈ 0.92
    assert news_freshness < 0.20  # 2h / 60 half-life ≈ 0.14
    assert flb_freshness > news_freshness


def test_infer_category_politics():
    assert _infer_category("Will the president win the election?") == "politics"


def test_infer_category_crypto():
    assert _infer_category("Will Bitcoin reach $100k?") == "crypto"


def test_infer_category_sports():
    assert _infer_category("Will the NBA finals go to game 7?") == "sports"


def test_infer_category_weather():
    assert _infer_category("Will a hurricane hit Florida?") == "weather"


def test_infer_category_general():
    assert _infer_category("Will AI pass the Turing test?") == "general"


@pytest.mark.asyncio
async def test_exposure_maxed_skips_signal(engine, market, mock_db):
    """When exposure is at the limit, signals should be short-circuited."""
    mock_db.get_total_exposure.return_value = 2500.0  # == bankroll * 0.50
    signal = Signal(
        source="llm", market_id="m1", direction=Direction.YES,
        confidence=0.95, reasoning="", timestamp=datetime.now(timezone.utc),
    )
    event = SignalEvent(signal=signal, market=market)
    await engine.on_signal(event)

    # Should NOT have saved signal or published anything
    engine._bus.publish.assert_not_called()


@pytest.mark.asyncio
async def test_single_source_blocked_even_with_auto_approve(mock_bus, mock_db):
    """auto_approve_all should NOT bypass min_signal_sources gate."""
    thresholds = ConfidenceThresholds(
        auto_execute=0.8, notify=0.5, auto_approve_all=True,
    )
    mock_risk = AsyncMock()
    mock_risk.check.return_value = (True, "Approved")
    mock_risk.calculate_position_size.return_value = 100.0
    mock_risk.circuit_breaker_active = False
    mock_risk._bankroll = 5000.0
    mock_risk._config = MagicMock()
    mock_risk._config.max_exposure_pct = 0.50

    eng = DecisionEngine(
        risk_manager=mock_risk, event_bus=mock_bus, database=mock_db,
        thresholds=thresholds, signals_config=SignalsConfig(),
    )

    market = Market(
        id="m1", question="Test?", end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.40,
    )
    signal = Signal(
        source="llm", market_id="m1", direction=Direction.YES,
        confidence=0.70, reasoning="", timestamp=datetime.now(timezone.utc),
    )
    event = SignalEvent(signal=signal, market=market)
    await eng.on_signal(event)

    # Single source with auto_approve: should downgrade to notify, not auto_execute
    calls = mock_bus.publish.call_args_list
    if calls:
        topics = [c[0][0] for c in calls]
        assert "trade_decision" not in topics
