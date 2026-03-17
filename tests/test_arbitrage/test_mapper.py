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
