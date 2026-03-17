import logging

logger = logging.getLogger(__name__)


class MarketMapper:
    def __init__(self, mappings: dict[str, dict[str, str]] | None = None):
        self._mappings: dict[str, dict[str, str]] = mappings or {}

    def get_mappings(self, polymarket_id: str) -> dict[str, str]:
        return self._mappings.get(polymarket_id, {})

    def all_polymarket_ids(self) -> list[str]:
        return list(self._mappings.keys())

    def add_mapping(self, polymarket_id: str, platform: str, platform_id: str) -> None:
        if polymarket_id not in self._mappings:
            self._mappings[polymarket_id] = {}
        self._mappings[polymarket_id][platform] = platform_id
        logger.info("Added mapping: %s -> %s:%s", polymarket_id, platform, platform_id)

    def remove_mapping(self, polymarket_id: str, platform: str | None = None) -> None:
        if platform:
            self._mappings.get(polymarket_id, {}).pop(platform, None)
        else:
            self._mappings.pop(polymarket_id, None)
