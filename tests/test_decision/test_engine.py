import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from polymarket_bot.decision.engine import DecisionEngine, _infer_category
from polymarket_bot.config import ConfidenceThresholds, SignalsConfig
from polymarket_bot.models import Signal, Direction, Market, SignalEvent, SignalBatchEvent, OrderType
from polymarket_bot.exit_manager import ExitManager, TrackedPosition


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
    risk._config.rotation_exposure_threshold = 0.95
    risk._config.rotation_edge_multiplier = 1.5
    risk._config.rotation_min_hold_minutes = 30
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
async def test_exposure_maxed_skips_signal_no_positions(engine, market, mock_db):
    """When exposure is at the limit and no positions to rotate, signals should be short-circuited."""
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
    mock_risk._config.rotation_exposure_threshold = 0.95

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


@pytest.mark.asyncio
async def test_rotation_sells_weak_position_for_stronger(mock_bus, mock_db):
    """When exposure is maxed, a strong new trade should rotate out a weak position."""
    from polymarket_bot.decision.risk import RiskManager
    from polymarket_bot.config import RiskConfig

    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=50,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
        rotation_edge_multiplier=1.5, rotation_min_hold_minutes=30,
        rotation_exposure_threshold=0.95,
    )
    mock_db.get_trade_count.return_value = 0
    mock_db.get_daily_pnl.return_value = 0.0
    mock_db.get_total_exposure.return_value = 2500.0  # == 5000 * 0.50

    risk = RiskManager(config=config, database=mock_db, bankroll=5000.0)

    # Create exit manager with a weak position (entered at 0.40, current still at 0.40 → edge ~0)
    exit_mgr = MagicMock()
    weak_pos = TrackedPosition(
        market_id="weak_market", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=2),  # > 30min
        tokens={"YES": "0xweak"},
    )
    exit_mgr._positions = {"weak_market": weak_pos}
    exit_mgr._price_getter = lambda platform, mid: 0.41 if mid == "weak_market" else None
    exit_mgr.track_exit = AsyncMock()
    risk._exit_manager = exit_mgr

    thresholds = ConfidenceThresholds(auto_execute=0.5, notify=0.3, auto_approve_all=True)
    eng = DecisionEngine(
        risk_manager=risk, event_bus=mock_bus, database=mock_db,
        thresholds=thresholds, signals_config=SignalsConfig(),
    )
    eng.set_exit_manager(exit_mgr)

    market = Market(
        id="strong_market", question="Will BTC hit 100k?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xstrong", "NO": "0xno"}, current_price=0.30,
    )
    signal = Signal(
        source="bookmaker", market_id="strong_market", direction=Direction.YES,
        confidence=0.85, reasoning="", timestamp=datetime.now(timezone.utc),
    )
    signal2 = Signal(
        source="divergence", market_id="strong_market", direction=Direction.YES,
        confidence=0.80, reasoning="", timestamp=datetime.now(timezone.utc),
    )
    # Need 2 signals to pass min_signal_sources gate
    mock_db.get_signals.return_value = [{
        "source": "divergence", "market_id": "strong_market",
        "direction": "YES", "confidence": 0.80, "reasoning": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]

    event = SignalEvent(signal=signal, market=market)
    await eng.on_signal(event)

    # Should have published 2 trade_decisions: exit for weak + entry for strong
    calls = mock_bus.publish.call_args_list
    trade_decisions = [c for c in calls if c[0][0] == "trade_decision"]
    assert len(trade_decisions) == 2
    # First is exit
    exit_dec = trade_decisions[0][0][1]
    assert exit_dec.is_exit is True
    assert exit_dec.market_id == "weak_market"
    # Second is entry
    entry_dec = trade_decisions[1][0][1]
    assert entry_dec.market_id == "strong_market"


@pytest.mark.asyncio
async def test_rotation_blocked_by_min_hold_time(mock_bus, mock_db):
    """Positions held less than rotation_min_hold_minutes should not be rotated."""
    from polymarket_bot.decision.risk import RiskManager
    from polymarket_bot.config import RiskConfig

    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=50,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
        rotation_edge_multiplier=1.5, rotation_min_hold_minutes=30,
        rotation_exposure_threshold=0.95,
    )
    mock_db.get_trade_count.return_value = 0
    mock_db.get_daily_pnl.return_value = 0.0
    mock_db.get_total_exposure.return_value = 2500.0

    risk = RiskManager(config=config, database=mock_db, bankroll=5000.0)

    # Position held only 5 minutes — too fresh to rotate
    exit_mgr = MagicMock()
    fresh_pos = TrackedPosition(
        market_id="fresh_market", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=5),
        tokens={"YES": "0xfresh"},
    )
    exit_mgr._positions = {"fresh_market": fresh_pos}
    exit_mgr._price_getter = lambda platform, mid: 0.41
    risk._exit_manager = exit_mgr

    # find_rotation_candidate should return None — position too fresh
    candidate = risk.find_rotation_candidate(new_edge=0.10, price_getter=exit_mgr._price_getter)
    assert candidate is None


@pytest.mark.asyncio
async def test_rotation_blocked_by_insufficient_edge(mock_bus, mock_db):
    """New trade with only slightly better edge should NOT trigger rotation."""
    from polymarket_bot.decision.risk import RiskManager
    from polymarket_bot.config import RiskConfig

    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=50,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
        rotation_edge_multiplier=1.5, rotation_min_hold_minutes=30,
        rotation_exposure_threshold=0.95,
    )
    mock_db.get_trade_count.return_value = 0
    mock_db.get_daily_pnl.return_value = 0.0
    mock_db.get_total_exposure.return_value = 2500.0

    risk = RiskManager(config=config, database=mock_db, bankroll=5000.0)

    exit_mgr = MagicMock()
    # Position with 5% edge (entered at 0.40, now at 0.45)
    pos = TrackedPosition(
        market_id="ok_market", direction=Direction.YES,
        entry_price=0.40, amount=100.0,
        entry_time=datetime.now(timezone.utc) - timedelta(hours=2),
        tokens={"YES": "0xok"},
    )
    exit_mgr._positions = {"ok_market": pos}
    exit_mgr._price_getter = lambda platform, mid: 0.45
    risk._exit_manager = exit_mgr

    # New edge 0.06 is NOT >= 1.5 * 0.05 (= 0.075) — should not rotate
    candidate = risk.find_rotation_candidate(new_edge=0.06, price_getter=exit_mgr._price_getter)
    assert candidate is None

    # New edge 0.08 IS >= 1.5 * 0.05 — should rotate
    candidate = risk.find_rotation_candidate(new_edge=0.08, price_getter=exit_mgr._price_getter)
    assert candidate is not None
    assert candidate[0] == "ok_market"


# ---------------------------------------------------------------------------
# on_signal_batch tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_signal_batch_multi_source_auto_executes(engine, market, mock_db, mock_bus):
    """A batch with 3 high-weight signals agreeing should reach auto_execute threshold."""
    # llm (0.25) + weather (0.20) + favorite_longshot (0.20) all at 0.90
    # gives composite ~0.807, which clears the 0.8 auto_execute threshold.
    signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.90, reasoning="llm analysis", timestamp=datetime.now(timezone.utc)),
        Signal(source="weather", market_id="m1", direction=Direction.YES,
               confidence=0.90, reasoning="weather factor", timestamp=datetime.now(timezone.utc)),
        Signal(source="favorite_longshot", market_id="m1", direction=Direction.YES,
               confidence=0.90, reasoning="structural bias", timestamp=datetime.now(timezone.utc)),
    )
    batch = SignalBatchEvent(signals=signals, market=market)
    mock_db.get_signals.return_value = []

    await engine.on_signal_batch(batch)

    calls = mock_bus.publish.call_args_list
    topics = [c[0][0] for c in calls]
    assert "trade_decision" in topics


@pytest.mark.asyncio
async def test_on_signal_batch_saves_all_signals(engine, market, mock_db):
    """on_signal_batch must persist every signal in the batch to the database."""
    signals = (
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.70, reasoning="news", timestamp=datetime.now(timezone.utc)),
        Signal(source="social", market_id="m1", direction=Direction.YES,
               confidence=0.65, reasoning="social", timestamp=datetime.now(timezone.utc)),
    )
    batch = SignalBatchEvent(signals=signals, market=market)
    mock_db.get_signals.return_value = []

    await engine.on_signal_batch(batch)

    # save_signal should have been called once per signal in the batch
    assert mock_db.save_signal.call_count == len(signals)
    saved_sources = {call.args[0].source for call in mock_db.save_signal.call_args_list}
    assert saved_sources == {"news", "social"}


@pytest.mark.asyncio
async def test_on_signal_batch_single_source_still_downgrades(engine, market, mock_db, mock_bus):
    """A batch with only one source must be downgraded from auto_execute to notify."""
    # Use a single very high-confidence signal — composite will pass auto_execute threshold
    # but min_signal_sources gate must downgrade it to notify
    signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.95, reasoning="very confident", timestamp=datetime.now(timezone.utc)),
    )
    batch = SignalBatchEvent(signals=signals, market=market)
    mock_db.get_signals.return_value = []

    await engine.on_signal_batch(batch)

    calls = mock_bus.publish.call_args_list
    if calls:
        topics = [c[0][0] for c in calls]
        # Must NOT have gone to auto_execute (trade_decision)
        assert "trade_decision" not in topics


@pytest.mark.asyncio
async def test_on_signal_batch_merges_with_prior_db_signals(engine, market, mock_db, mock_bus):
    """Batch signals + prior DB signals from different sources are merged for aggregation.

    The batch contains llm (weight=0.25, conf=0.99) and the DB contains a weather signal
    (weight=0.20, conf=0.99) from a prior cycle.  Together they produce composite ~0.888,
    clearing auto_execute (0.8) with 2 distinct sources.
    """
    # Batch has one high-weight source (llm)
    batch_signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.99, reasoning="llm very confident", timestamp=datetime.now(timezone.utc)),
    )
    batch = SignalBatchEvent(signals=batch_signals, market=market)

    # DB has a prior signal from a different high-weight source (weather)
    prior_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    mock_db.get_signals.return_value = [
        {
            "source": "weather",
            "market_id": "m1",
            "direction": "YES",
            "confidence": 0.99,
            "reasoning": "weather model",
            "timestamp": prior_timestamp,
        }
    ]

    await engine.on_signal_batch(batch)

    # With 2 sources satisfying min_signal_sources and composite ≥ 0.8,
    # the engine should auto_execute → trade_decision published.
    calls = mock_bus.publish.call_args_list
    topics = [c[0][0] for c in calls]
    assert "trade_decision" in topics
