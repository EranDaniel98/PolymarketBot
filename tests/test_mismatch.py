import pytest
from polymarket_weather.trading.mismatch import (
    compute_edge, compute_kelly_size, filter_opportunity,
    get_min_edge_for_source, OpportunitySignal,
)


# --- Edge computation ---

def test_compute_edge_yes():
    edge = compute_edge(our_p=0.75, market_p=0.55)
    assert edge.raw_edge == pytest.approx(0.20)
    assert edge.direction == "YES"
    assert edge.ev == pytest.approx(0.20)


def test_compute_edge_no():
    edge = compute_edge(our_p=0.30, market_p=0.60)
    assert edge.direction == "NO"
    assert edge.raw_edge == pytest.approx(0.30)


def test_compute_edge_zero():
    edge = compute_edge(our_p=0.55, market_p=0.55)
    assert edge.raw_edge == pytest.approx(0.0)
    assert edge.direction == "YES"


# --- Kelly sizing ---

def test_kelly_size_yes():
    size = compute_kelly_size(
        edge=0.20, market_price=0.55, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert 5 <= size <= 50


def test_kelly_size_no():
    size = compute_kelly_size(
        edge=0.20, market_price=0.60, direction="NO",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert 5 <= size <= 50


def test_kelly_clamped_max():
    size = compute_kelly_size(
        edge=0.50, market_price=0.30, direction="YES",
        bankroll=10000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 50


def test_kelly_below_min():
    size = compute_kelly_size(
        edge=0.001, market_price=0.50, direction="YES",
        bankroll=100, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 0


def test_kelly_extreme_price_high():
    size = compute_kelly_size(
        edge=0.02, market_price=0.99, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size <= 50


def test_kelly_extreme_price_low():
    size = compute_kelly_size(
        edge=0.02, market_price=0.01, direction="NO",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size <= 50


def test_kelly_fee_exceeds_edge():
    size = compute_kelly_size(
        edge=0.005, market_price=0.50, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 0  # Fee > edge → don't trade


def test_kelly_zero_edge():
    size = compute_kelly_size(
        edge=0.0, market_price=0.50, direction="YES",
        bankroll=1000, kelly_fraction=0.5, fee=0.01,
        max_position=50, min_position=5,
    )
    assert size == 0


# --- Filter opportunity ---

def test_filter_passes():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=False,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is True


def test_filter_low_edge():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.60, market_p=0.55, edge=0.05,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=False,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is False


def test_filter_low_confidence():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.50, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=False,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is False


def test_filter_stale_station():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=4.0, station_stale=True,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is False


def test_filter_too_soon():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=1.0, station_stale=False,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is False


def test_filter_too_far():
    opp = OpportunitySignal(
        market_id="0xabc", our_p=0.75, market_p=0.55, edge=0.20,
        direction="YES", confidence=0.85, forecast_source="metar",
        hours_to_resolution=200.0, station_stale=False,
    )
    assert filter_opportunity(opp, min_edge=0.12, min_confidence=0.70,
                              min_hours=2, max_hours=168) is False


# --- Min edge by source ---

def test_min_edge_metar():
    assert get_min_edge_for_source("metar") == 0.06


def test_min_edge_blend():
    assert get_min_edge_for_source("metar_nwp") == 0.08


def test_min_edge_nwp():
    assert get_min_edge_for_source("nwp_ensemble") == 0.12


def test_min_edge_unknown():
    assert get_min_edge_for_source("unknown") == 0.12
