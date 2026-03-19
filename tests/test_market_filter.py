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


def test_filters_extreme_prices(mf):
    markets = [_make_market(price=0.01), _make_market(price=0.99)]
    result = mf.filter_and_rank(markets)
    assert len(result) == 0


def test_filters_no_tokens(mf):
    m = _make_market()
    m.tokens = {}
    result = mf.filter_and_rank([m])
    assert len(result) == 0


def test_filters_too_far_out(mf):
    result = mf.filter_and_rank([_make_market(days_out=200)])
    assert len(result) == 0


def test_prefers_uncertain_markets(mf):
    m_50 = _make_market(price=0.50)  # Max uncertainty
    m_80 = _make_market(price=0.80)  # Lower uncertainty
    result = mf.filter_and_rank([m_80, m_50])
    assert result[0].current_price == 0.50  # 0.50 ranked first


def test_politics_gets_bonus(mf):
    pol = _make_market(category="politics")
    gen = _make_market(category="misc")
    result = mf.filter_and_rank([gen, pol])
    assert result[0].category == "politics"
