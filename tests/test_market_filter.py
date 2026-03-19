import pytest
from datetime import datetime, timezone, timedelta
from polymarket_bot.market_filter import MarketFilter
from polymarket_bot.models import Market


@pytest.fixture
def mf():
    return MarketFilter()


def _make_market(price=0.5, days_out=15, category="politics", question="Will X happen?"):
    return Market(
        id="m1", question=question,
        end_date=datetime.now(timezone.utc) + timedelta(days=days_out),
        tokens={"YES": "a", "NO": "b"}, current_price=price,
        category=category,
    )


def _make_market_with_vol(price=0.5, days_out=15, category="politics", volume=10000):
    return Market(
        id="m1", question="Will X happen?",
        end_date=datetime.now(timezone.utc) + timedelta(days=days_out),
        tokens={"YES": "a", "NO": "b"}, current_price=price,
        category=category, volume=volume,
    )


def test_filters_very_extreme_prices(mf):
    """Prices at 0.01 and 0.99 should be filtered out."""
    markets = [_make_market(price=0.01), _make_market(price=0.99)]
    result = mf.filter_and_rank(markets)
    assert len(result) == 0


def test_filters_no_tokens(mf):
    m = _make_market()
    m.tokens = {}
    result = mf.filter_and_rank([m])
    assert len(result) == 0


def test_filters_too_far_out(mf):
    result = mf.filter_and_rank([_make_market(days_out=400)])
    assert len(result) == 0


def test_prefers_edge_markets(mf):
    m_50 = _make_market(price=0.50)  # Efficient — hard to find edge
    m_30 = _make_market(price=0.30)  # Leaning — more edge potential
    result = mf.filter_and_rank([m_50, m_30])
    assert result[0].current_price == 0.30  # 0.30 ranked first (edge zone)


def test_politics_gets_bonus(mf):
    pol = _make_market(category="politics")
    gen = _make_market(category="misc")
    result = mf.filter_and_rank([gen, pol])
    assert result[0].category == "politics"


def test_extreme_price_passes_with_volume(mf):
    """High-price market with volume should pass through for FLB."""
    m = _make_market_with_vol(price=0.93, volume=10000, days_out=15)
    result = mf.filter_and_rank([m])
    assert len(result) == 1


def test_extreme_low_price_passes_with_volume(mf):
    """Low-price market with volume should pass through for FLB."""
    m = _make_market_with_vol(price=0.05, volume=10000, days_out=15)
    result = mf.filter_and_rank([m])
    assert len(result) == 1
