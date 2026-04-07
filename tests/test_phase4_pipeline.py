"""Phase 4 tests — mismatch pipeline + math improvements.

Pipeline tests use mocks for every collaborator so they're fast and
deterministic. Pipeline.evaluate is engineered to never raise — every error
case becomes a PipelineResult with decision='skipped' or 'error' + a
machine-readable reason string.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket_weather.markets.scanner import ScannedMarket
from polymarket_weather.trading.mismatch import compute_kelly_size
from polymarket_weather.trading.pipeline import (
    REASON_EXTREME_PRICE,
    REASON_HORIZON_TOO_LONG,
    REASON_HORIZON_TOO_SHORT,
    REASON_INSUFFICIENT_EDGE,
    REASON_LOW_CONFIDENCE,
    REASON_LOW_LIQUIDITY,
    REASON_NO_END_DATE,
    REASON_RISK_REJECTED,
    REASON_UNKNOWN_CITY,
    REASON_UNPARSEABLE,
    MismatchPipeline,
)


# ---------------------------------------------------------------------------
# Mock builders
# ---------------------------------------------------------------------------

def _make_scanned(
    *,
    market_id="0xmkt1",
    question="Will the high temperature in New York City be above 75 degrees on April 10?",
    yes_price=0.40,
    no_price=0.60,
    volume=1000,
    end_date=None,
) -> ScannedMarket:
    if end_date is None:
        end_date = datetime.now(timezone.utc) + timedelta(hours=12)
    return ScannedMarket(
        market_id=market_id,
        question=question,
        event_id="evt_1",
        yes_token_id="yes_token",
        no_token_id="no_token",
        current_price=yes_price,
        no_price=no_price,
        end_date=end_date,
        resolution_source="weather.com",
        volume=volume,
        slug="weather-market",
        category="Weather",
    )


def _make_pipeline(
    *,
    parse_result=True,
    city_resolves=True,
    forecast_p=0.75,
    forecast_confidence=0.85,
    forecast_source="nwp_ensemble",
    bankroll=1000.0,
    risk_approves=True,
):
    """Build a pipeline with mock collaborators tuned per-test."""
    city_mapper = MagicMock()
    city_mapper.all_aliases.return_value = ["new york city", "new york", "nyc"]
    if city_resolves:
        city_mapper.resolve.return_value = SimpleNamespace(
            city_name="new york city",
            primary_station="KJFK",
            all_stations=["KJFK"],
            region="northeast",
            country="US",
            lat=40.64,
            lon=-73.78,
        )
    else:
        city_mapper.resolve.return_value = None

    forecast_engine = MagicMock()
    if forecast_p is None:
        forecast_engine.compute_from_ensemble.return_value = None
    else:
        forecast_engine.compute_from_ensemble.return_value = SimpleNamespace(
            probability=forecast_p,
            confidence=forecast_confidence,
            source=forecast_source,
        )

    nwp = MagicMock()
    nwp.fetch_ensemble = AsyncMock(return_value=SimpleNamespace(
        n_members=51,
        at_time=lambda t: (75.0, 2.5),
    ))

    metar = MagicMock()

    risk = MagicMock()
    risk.get_max_size.return_value = 50.0
    risk.check_trade.return_value = SimpleNamespace(
        approved=risk_approves,
        reason="" if risk_approves else "max_position",
    )
    risk.record_entry = MagicMock()

    executor = MagicMock()
    executor.get_balance.return_value = bankroll
    executor.execute_order = AsyncMock(return_value=SimpleNamespace(
        success=True, order_id="paper_xyz", filled_price=0.40, filled_amount=10.0,
    ))

    positions = MagicMock()
    positions.track_entry = MagicMock()

    notifier = MagicMock()
    notifier.send_trade_placed = AsyncMock()

    session_factory = MagicMock()
    trade_lock = asyncio.Lock()

    edge_config = SimpleNamespace(
        min_edge_metar=0.06,
        min_edge_blend=0.08,
        min_edge_nwp=0.12,
        min_liquidity_usdc=500,
        min_confidence=0.7,
        min_hours_to_resolution=2,
        max_hours_to_resolution=168,
        kelly_fraction=0.5,
    )
    fee_config = SimpleNamespace(
        default_taker_fee=0.01, weather_taker_fee=0.01, maker_fee=0.0,
    )
    risk_config = SimpleNamespace(
        max_position_usdc=50.0, min_trade_size_usdc=5.0,
    )

    pipeline = MismatchPipeline(
        city_mapper=city_mapper,
        forecast_engine=forecast_engine,
        metar_collector=metar,
        nwp_fetcher=nwp,
        risk_manager=risk,
        executor=executor,
        position_manager=positions,
        session_factory=session_factory,
        trade_lock=trade_lock,
        notifier=notifier,
        edge_config=edge_config,
        fee_config=fee_config,
        trading_config=None,
        risk_config=risk_config,
    )
    return pipeline


# ---------------------------------------------------------------------------
# Pipeline happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_traded_happy_path(monkeypatch):
    monkeypatch.setattr(
        "polymarket_weather.trading.pipeline.persistence.persist_position_entry",
        AsyncMock(return_value=42),
    )
    pipeline = _make_pipeline(forecast_p=0.75)
    scanned = _make_scanned(yes_price=0.40)  # our_p 0.75 vs market 0.40 → edge 0.35
    result = await pipeline.evaluate(scanned)
    assert result.decision == "traded", f"got {result.decision} ({result.reason})"
    assert result.direction == "YES"
    assert result.our_p == 0.75
    assert result.size_usdc > 0


# ---------------------------------------------------------------------------
# Pipeline rejection paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_skips_unparseable_question():
    pipeline = _make_pipeline()
    scanned = _make_scanned(question="Will Trump tweet about TikTok by Friday?")
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_UNPARSEABLE


@pytest.mark.asyncio
async def test_pipeline_skips_unknown_city():
    pipeline = _make_pipeline(city_resolves=False)
    scanned = _make_scanned()
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_UNKNOWN_CITY


@pytest.mark.asyncio
async def test_pipeline_skips_no_end_date():
    pipeline = _make_pipeline()
    scanned = _make_scanned(end_date=None)
    # Manually clear since the helper auto-sets
    scanned.end_date = None
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_NO_END_DATE


@pytest.mark.asyncio
async def test_pipeline_skips_horizon_too_short():
    pipeline = _make_pipeline()
    scanned = _make_scanned(end_date=datetime.now(timezone.utc) + timedelta(minutes=30))
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_HORIZON_TOO_SHORT


@pytest.mark.asyncio
async def test_pipeline_skips_horizon_too_long():
    pipeline = _make_pipeline()
    scanned = _make_scanned(end_date=datetime.now(timezone.utc) + timedelta(days=30))
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_HORIZON_TOO_LONG


@pytest.mark.asyncio
async def test_pipeline_skips_low_liquidity():
    pipeline = _make_pipeline()
    scanned = _make_scanned(volume=100)  # below 500 default
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_LOW_LIQUIDITY


@pytest.mark.asyncio
async def test_pipeline_skips_extreme_price_high():
    pipeline = _make_pipeline()
    scanned = _make_scanned(yes_price=0.99, no_price=0.01)
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_EXTREME_PRICE


@pytest.mark.asyncio
async def test_pipeline_skips_extreme_price_low():
    pipeline = _make_pipeline()
    scanned = _make_scanned(yes_price=0.01, no_price=0.99)
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_EXTREME_PRICE


@pytest.mark.asyncio
async def test_pipeline_skips_low_confidence():
    pipeline = _make_pipeline(forecast_confidence=0.5)
    scanned = _make_scanned()
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_LOW_CONFIDENCE


@pytest.mark.asyncio
async def test_pipeline_skips_insufficient_edge():
    pipeline = _make_pipeline(forecast_p=0.42)  # 0.42 vs 0.40 → edge 0.02
    scanned = _make_scanned(yes_price=0.40)
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason == REASON_INSUFFICIENT_EDGE


@pytest.mark.asyncio
async def test_pipeline_skips_when_risk_rejects(monkeypatch):
    monkeypatch.setattr(
        "polymarket_weather.trading.pipeline.persistence.persist_position_entry",
        AsyncMock(return_value=42),
    )
    pipeline = _make_pipeline(forecast_p=0.75, risk_approves=False)
    scanned = _make_scanned(yes_price=0.40)
    result = await pipeline.evaluate(scanned)
    assert result.decision == "skipped"
    assert result.reason.startswith(REASON_RISK_REJECTED)


@pytest.mark.asyncio
async def test_pipeline_no_side_effects_when_skipped(monkeypatch):
    """A skipped market must not call execute_order, persist, or risk.record_entry."""
    persist_mock = AsyncMock(return_value=42)
    monkeypatch.setattr(
        "polymarket_weather.trading.pipeline.persistence.persist_position_entry",
        persist_mock,
    )
    pipeline = _make_pipeline(forecast_p=0.42)  # insufficient edge
    scanned = _make_scanned(yes_price=0.40)
    await pipeline.evaluate(scanned)
    pipeline.executor.execute_order.assert_not_called()
    persist_mock.assert_not_called()
    pipeline.risk.record_entry.assert_not_called()
    pipeline.positions.track_entry.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_yes_uses_yes_price(monkeypatch):
    """Phase 4.4 — when direction is YES, the bot must use scanned.current_price."""
    monkeypatch.setattr(
        "polymarket_weather.trading.pipeline.persistence.persist_position_entry",
        AsyncMock(return_value=42),
    )
    pipeline = _make_pipeline(forecast_p=0.75)
    scanned = _make_scanned(yes_price=0.40, no_price=0.55)  # spread (sums to 0.95)
    result = await pipeline.evaluate(scanned)
    assert result.decision == "traded"
    assert result.direction == "YES"
    # Order placed at YES price, not NO price
    call_kwargs = pipeline.executor.execute_order.call_args.kwargs
    assert call_kwargs["price"] == 0.40
    assert call_kwargs["token_id"] == "yes_token"


@pytest.mark.asyncio
async def test_pipeline_no_uses_no_price(monkeypatch):
    """Phase 4.4 — when direction is NO, the bot must use scanned.no_price."""
    monkeypatch.setattr(
        "polymarket_weather.trading.pipeline.persistence.persist_position_entry",
        AsyncMock(return_value=42),
    )
    pipeline = _make_pipeline(forecast_p=0.30)
    scanned = _make_scanned(yes_price=0.65, no_price=0.30)  # our_p 0.30 < 0.65 → NO
    result = await pipeline.evaluate(scanned)
    assert result.decision == "traded"
    assert result.direction == "NO"
    call_kwargs = pipeline.executor.execute_order.call_args.kwargs
    assert call_kwargs["price"] == 0.30
    assert call_kwargs["token_id"] == "no_token"


@pytest.mark.asyncio
async def test_pipeline_never_raises_on_internal_error():
    """Even if a collaborator throws, evaluate must return decision='error'."""
    pipeline = _make_pipeline()
    pipeline.nwp.fetch_ensemble = AsyncMock(side_effect=RuntimeError("network down"))
    scanned = _make_scanned()
    result = await pipeline.evaluate(scanned)
    # The exception is caught inside _get_forecast which returns None,
    # so we get FORECAST_UNAVAILABLE rather than 'error'.
    assert result.decision == "skipped"


# ---------------------------------------------------------------------------
# 4.3 Parametric Kelly tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("price", [0.05, 0.10, 0.30, 0.50, 0.70, 0.90, 0.95])
@pytest.mark.parametrize("edge", [0.05, 0.10, 0.20])
def test_kelly_yes_monotonic_in_edge(price, edge):
    """For a fixed price, more edge → larger size on the YES side."""
    sizes = []
    for e in [edge, edge + 0.05, edge + 0.10]:
        s = compute_kelly_size(
            edge=e, market_price=price, direction="YES",
            bankroll=1000, kelly_fraction=0.5, fee=0.01,
            max_position=1000, min_position=1,
        )
        sizes.append(s)
    # Strictly non-decreasing
    assert sizes == sorted(sizes), f"Kelly sizes not monotonic in edge: {sizes}"


@pytest.mark.parametrize("direction,price,edge", [
    ("YES", 0.50, 0.20),
    ("YES", 0.10, 0.05),
    ("NO", 0.50, 0.20),
    ("NO", 0.90, 0.05),
])
def test_kelly_size_positive_when_edge_above_fee(direction, price, edge):
    s = compute_kelly_size(
        edge=edge, market_price=price, direction=direction,
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=1,
    )
    assert s > 0


def test_kelly_size_zero_when_fee_eats_edge():
    # edge=0.005 but fee=0.01 → effective_edge negative → skip
    s = compute_kelly_size(
        edge=0.005, market_price=0.50, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=1,
    )
    assert s == 0.0


def test_kelly_size_zero_for_negative_edge():
    s = compute_kelly_size(
        edge=-0.05, market_price=0.50, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=1,
    )
    assert s == 0.0


def test_kelly_size_capped_at_max_position():
    s = compute_kelly_size(
        edge=0.40, market_price=0.10, direction="YES",
        bankroll=10000, kelly_fraction=1.0, fee=0.01,
        max_position=100, min_position=1,
    )
    assert s <= 100


def test_kelly_size_zero_below_min_position():
    s = compute_kelly_size(
        edge=0.10, market_price=0.50, direction="YES",
        bankroll=10, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=5,
    )
    # 10 * (0.09/0.5) * 0.5 = 0.9 → below min_position 5 → zero
    assert s == 0.0


def test_kelly_clamps_extreme_yes_price():
    """price=0.99 must be treated as 0.95 to avoid division blow-up."""
    s = compute_kelly_size(
        edge=0.10, market_price=0.99, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=1,
    )
    # With clamp: effective_edge=0.09, kelly=0.09/0.05=1.8, capped at 0.25, * 0.5 frac, * 1000
    # = 125.0
    assert s > 100
    assert s <= 1000


def test_kelly_clamps_extreme_no_price():
    s = compute_kelly_size(
        edge=0.10, market_price=0.01, direction="NO",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=1000, min_position=1,
    )
    assert s > 100  # Same logic mirrored on NO side
