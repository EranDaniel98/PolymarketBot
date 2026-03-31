import pytest
from unittest.mock import AsyncMock
from polymarket_bot.decision.risk import RiskManager, half_kelly, calculate_round_trip_fee
from polymarket_bot.config import FeeConfig, RiskConfig
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
    size = await risk_manager.calculate_position_size(confidence=0.9, market_price=0.50)
    assert size == 50.0


async def test_calculate_position_size_kelly(risk_manager, mock_db):
    mock_db.get_trade_count.return_value = 51
    size = await risk_manager.calculate_position_size(confidence=0.8, market_price=0.40)
    assert size <= 250.0


async def test_check_risk_passes(risk_manager):
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.85, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is True


async def test_check_risk_rejects_low_edge(risk_manager):
    # Very low confidence → estimated probability barely above market → edge too small
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.15, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.50)
    assert approved is False
    assert "edge" in reason.lower()


async def test_check_risk_rejects_circuit_breaker(risk_manager, mock_db):
    mock_db.get_daily_pnl.return_value = -600.0
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "circuit breaker" in reason.lower()


async def test_check_risk_rejects_max_exposure(risk_manager, mock_db):
    mock_db.get_total_exposure.return_value = 2600.0
    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=100.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
    )
    approved, reason = await risk_manager.check(decision, market_price=0.30)
    assert approved is False
    assert "exposure" in reason.lower()


async def test_half_kelly_formula():
    # Fee-adjusted odds: b = (1-1/3)/(1/3) * (1-0.01) = 2.0 * 0.99 = 1.98
    # full = (0.7*1.98 - 0.3)/1.98 = (1.386-0.3)/1.98 = 0.548, half = 0.274
    result = half_kelly(p=0.7, market_price=1/3, fraction=0.5)
    assert 0.20 < result < 0.30


async def test_tiered_kelly_low_confidence(risk_manager, mock_db):
    """Low confidence (post-discount) should use quarter-Kelly (0.25)."""
    mock_db.get_trade_count.return_value = 51
    rm = risk_manager
    assert rm._kelly_fraction_for_confidence(0.45) == 0.25


async def test_tiered_kelly_medium_confidence(risk_manager):
    """Mid-range post-discount confidence should use 0.35 Kelly."""
    assert risk_manager._kelly_fraction_for_confidence(0.57) == 0.35


async def test_tiered_kelly_high_confidence(risk_manager):
    """High post-discount confidence should use full configured Kelly."""
    assert risk_manager._kelly_fraction_for_confidence(0.70) == 0.5


async def test_circuit_breaker_reset(risk_manager, mock_db):
    """Circuit breaker should reset when daily PnL recovers to half threshold."""
    # Trigger circuit breaker
    risk_manager._circuit_breaker_active = True
    # PnL recovered to -200 (threshold is -250 = half of -500)
    mock_db.get_daily_pnl.return_value = -200.0
    reset = await risk_manager.maybe_reset_circuit_breaker()
    assert reset is True
    assert risk_manager._circuit_breaker_active is False
    assert risk_manager._recovery_until is not None


async def test_circuit_breaker_no_reset_still_bad(risk_manager, mock_db):
    """Circuit breaker should NOT reset when PnL is still too negative."""
    risk_manager._circuit_breaker_active = True
    mock_db.get_daily_pnl.return_value = -400.0  # Still worse than -250
    reset = await risk_manager.maybe_reset_circuit_breaker()
    assert reset is False
    assert risk_manager._circuit_breaker_active is True


async def test_recovery_reduces_sizing(mock_db):
    """During recovery, position sizes should be halved."""
    config = RiskConfig(
        max_position_pct=0.05, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=50,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
        recovery_hours=2, recovery_sizing_pct=0.50,
    )
    rm = RiskManager(config=config, database=mock_db, bankroll=5000.0)

    # Normal sizing
    normal_size = await rm.calculate_position_size(confidence=0.9, market_price=0.50)

    # Set recovery mode
    from datetime import timedelta
    rm._recovery_until = datetime.now(timezone.utc) + timedelta(hours=1)
    recovery_size = await rm.calculate_position_size(confidence=0.9, market_price=0.50)

    assert recovery_size == pytest.approx(normal_size * 0.5)


async def test_correlated_exposure_rejected(mock_db):
    """Trades exceeding correlated exposure limit should be rejected."""
    from unittest.mock import MagicMock
    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        max_correlated_exposure_pct=0.10, min_edge=0.03, kelly_fraction=0.5,
        bootstrap_trades=50, bootstrap_size_pct=0.01, cooldown_seconds=300,
    )
    exit_mgr = MagicMock()
    exit_mgr.get_correlated_exposure.return_value = 400.0  # Already $400 in politics

    rm = RiskManager(config=config, database=mock_db, bankroll=5000.0, exit_manager=exit_mgr)

    decision = TradeDecision(
        market_id="m1", direction=Direction.YES, amount=200.0,
        confidence=0.9, signals=[], order_type=OrderType.LIMIT,
        category="politics",
    )
    approved, reason = await rm.check(decision, market_price=0.30)
    assert approved is False
    assert "correlated" in reason.lower()


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


# --- Dynamic fee model tests ---


def test_calculate_round_trip_fee_maker():
    """Maker orders always pay 0%."""
    assert calculate_round_trip_fee("crypto", is_maker=True) == 0.0
    assert calculate_round_trip_fee("sports", is_maker=True) == 0.0


def test_calculate_round_trip_fee_geopolitics():
    """Geopolitics markets are fee-free."""
    assert calculate_round_trip_fee("geopolitics") == 0.0


def test_calculate_round_trip_fee_politics():
    """Politics taker fee is 1% (changed March 30, 2026)."""
    assert calculate_round_trip_fee("politics") == 0.01


def test_calculate_round_trip_fee_crypto():
    """Crypto taker fee is 1.8% (increased March 30, 2026)."""
    assert calculate_round_trip_fee("crypto") == 0.018


def test_calculate_round_trip_fee_sports():
    """Sports taker fee is 0.75%."""
    assert calculate_round_trip_fee("sports") == 0.0075


def test_calculate_round_trip_fee_default_fallback():
    """Unknown categories use the default taker fee."""
    assert calculate_round_trip_fee("unknown_category") == 0.01
    assert calculate_round_trip_fee("") == 0.01


def test_calculate_round_trip_fee_with_config():
    """FeeConfig overrides built-in category defaults."""
    cfg = FeeConfig(
        default_taker_fee=0.02,
        category_taker_fees={"crypto": 0.005, "special": 0.0},
    )
    assert calculate_round_trip_fee("crypto", fee_config=cfg) == 0.005
    assert calculate_round_trip_fee("special", fee_config=cfg) == 0.0
    assert calculate_round_trip_fee("other", fee_config=cfg) == 0.02  # default


def test_half_kelly_with_lower_fee_gives_larger_position():
    """Lower fees should produce larger Kelly fractions."""
    size_high_fee = half_kelly(p=0.65, market_price=0.50, fraction=0.5, round_trip_fee=0.03)
    size_low_fee = half_kelly(p=0.65, market_price=0.50, fraction=0.5, round_trip_fee=0.0)
    size_default = half_kelly(p=0.65, market_price=0.50, fraction=0.5)
    assert size_low_fee > size_default > size_high_fee


def test_half_kelly_backward_compat():
    """Default round_trip_fee=0.01 produces reasonable results."""
    result = half_kelly(p=0.7, market_price=1/3, fraction=0.5)
    assert 0.20 < result < 0.30


async def test_position_size_with_category(mock_db):
    """Category-aware sizing: geopolitics (0% fee) should give equal or larger sizes."""
    config = RiskConfig(
        max_position_pct=0.10, max_exposure_pct=0.50, max_daily_loss_pct=0.10,
        min_edge=0.03, kelly_fraction=0.5, bootstrap_trades=10,
        bootstrap_size_pct=0.01, cooldown_seconds=300,
    )
    mock_db.get_trade_count.return_value = 50  # Past bootstrap
    rm = RiskManager(config=config, database=mock_db, bankroll=5000.0)
    size_default = await rm.calculate_position_size(0.7, 0.50, category="crypto")
    size_geo = await rm.calculate_position_size(0.7, 0.50, category="geopolitics")
    assert size_geo >= size_default
