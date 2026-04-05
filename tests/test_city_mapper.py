import pytest
import json
from pathlib import Path
from polymarket_weather.weather.city_mapper import CityMapper, CityMatch


@pytest.fixture
def mapper(tmp_path):
    cities = [
        {"city_aliases": ["new york", "nyc", "new york city"],
         "stations": ["KJFK", "KLGA", "KEWR"],
         "primary_station": "KJFK", "region": "northeast_us",
         "country": "US", "lat": 40.6413, "lon": -73.7781},
        {"city_aliases": ["tokyo"],
         "stations": ["RJTT", "RJAA"],
         "primary_station": "RJTT", "region": "kanto_jp",
         "country": "JP", "lat": 35.5494, "lon": 139.7798},
        {"city_aliases": ["los angeles", "la"],
         "stations": ["KLAX", "KVNY"],
         "primary_station": "KLAX", "region": "southwest_us",
         "country": "US", "lat": 33.9425, "lon": -118.4081},
    ]
    cities_file = tmp_path / "cities.json"
    cities_file.write_text(json.dumps(cities))
    return CityMapper(cities_file)


def test_resolve_exact_match(mapper):
    result = mapper.resolve("new york")
    assert result is not None
    assert result.primary_station == "KJFK"
    assert result.region == "northeast_us"
    assert result.country == "US"


def test_resolve_alias(mapper):
    result = mapper.resolve("nyc")
    assert result is not None
    assert result.primary_station == "KJFK"


def test_resolve_case_insensitive(mapper):
    result = mapper.resolve("NYC")
    assert result is not None
    assert result.primary_station == "KJFK"


def test_resolve_unknown_city(mapper):
    result = mapper.resolve("atlantis")
    assert result is None


def test_all_stations(mapper):
    result = mapper.resolve("new york")
    assert set(result.all_stations) == {"KJFK", "KLGA", "KEWR"}


def test_all_city_names(mapper):
    cities = mapper.all_city_names()
    assert "new york" in cities
    assert "tokyo" in cities
    assert "los angeles" in cities


def test_all_aliases(mapper):
    aliases = mapper.all_aliases()
    assert "nyc" in aliases
    assert "la" in aliases


def test_all_station_ids(mapper):
    stations = mapper.all_station_ids()
    assert "KJFK" in stations
    assert "RJTT" in stations
    assert "KLAX" in stations


def test_get_region(mapper):
    assert mapper.get_region("new york") == "northeast_us"
    assert mapper.get_region("tokyo") == "kanto_jp"
    assert mapper.get_region("unknown") is None


def test_resolve_with_whitespace(mapper):
    result = mapper.resolve("  new york  ")
    assert result is not None


def test_real_cities_json():
    """Verify the actual cities.json seed data is valid."""
    cities_file = Path(__file__).parent.parent / "config" / "cities.json"
    if not cities_file.exists():
        pytest.skip("cities.json not found")
    mapper = CityMapper(cities_file)
    # Should have 20+ cities
    assert len(mapper.all_city_names()) >= 20
    # All entries should resolve
    for name in mapper.all_city_names():
        result = mapper.resolve(name)
        assert result is not None, f"Failed to resolve {name}"
        assert len(result.primary_station) == 4, f"Bad station for {name}: {result.primary_station}"
