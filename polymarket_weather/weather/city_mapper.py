from dataclasses import dataclass
from pathlib import Path
import json


@dataclass
class CityMatch:
    city_name: str           # Canonical name (first alias)
    primary_station: str     # Primary ICAO station
    all_stations: list[str]  # All nearby stations
    region: str              # Geographic region for correlation
    country: str             # ISO-3166 country code
    lat: float
    lon: float


class CityMapper:
    def __init__(self, cities_file: Path):
        with open(cities_file) as f:
            self._cities = json.load(f)
        # Build alias lookup: lowercase alias -> city dict
        self._alias_map: dict[str, dict] = {}
        for city in self._cities:
            for alias in city["city_aliases"]:
                self._alias_map[alias.lower()] = city

    def resolve(self, city_name: str) -> CityMatch | None:
        """Resolve a city name (or alias) to ICAO station info."""
        city = self._alias_map.get(city_name.lower().strip())
        if not city:
            return None
        return CityMatch(
            city_name=city["city_aliases"][0],
            primary_station=city["primary_station"],
            all_stations=city["stations"],
            region=city["region"],
            country=city["country"],
            lat=city["lat"],
            lon=city["lon"],
        )

    def all_city_names(self) -> list[str]:
        """Return canonical names for all configured cities."""
        return [c["city_aliases"][0] for c in self._cities]

    def all_aliases(self) -> list[str]:
        """Return all known aliases (for market question matching)."""
        return list(self._alias_map.keys())

    def all_station_ids(self) -> list[str]:
        """Return all primary station IDs (for METAR polling)."""
        return list({c["primary_station"] for c in self._cities})

    def get_region(self, city_name: str) -> str | None:
        """Get region for correlation checking."""
        match = self.resolve(city_name)
        return match.region if match else None
