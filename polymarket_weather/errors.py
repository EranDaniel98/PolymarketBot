"""Custom exception types for the polymarket_weather package.

Phase 5.2 — replaces bare ValueError / returning None in trading & forecast
paths so callers can react with type-driven dispatch instead of string
matching on error messages.

All exceptions inherit from PolymarketWeatherError so external code can
catch the whole hierarchy with a single `except`.
"""

from __future__ import annotations


class PolymarketWeatherError(Exception):
    """Base class for all package-specific exceptions."""


# ---------------------------------------------------------------------------
# Trading-pipeline errors
# ---------------------------------------------------------------------------

class TradingError(PolymarketWeatherError):
    """Base for any error during the trading pipeline."""


class InsufficientEdgeError(TradingError):
    """Raised when an opportunity's edge is below the minimum threshold."""


class RiskLimitError(TradingError):
    """Raised when a trade would breach a configured risk limit."""

    def __init__(self, reason: str, *, limit: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.limit = limit


class InvalidMarketError(TradingError):
    """Raised when market data is malformed or unsuitable for trading."""


# ---------------------------------------------------------------------------
# Data / forecast errors
# ---------------------------------------------------------------------------

class DataError(PolymarketWeatherError):
    """Base for errors involving weather data or market data sources."""


class StaleDataError(DataError):
    """Raised when the source data is too old to be trustworthy."""

    def __init__(self, source: str, age_seconds: float) -> None:
        super().__init__(f"{source} data is stale ({age_seconds:.0f}s old)")
        self.source = source
        self.age_seconds = age_seconds


class ForecastUnavailableError(DataError):
    """Raised when no forecast can be produced (no readings, API down, etc.)."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------

class ConfigError(PolymarketWeatherError):
    """Raised when the bot is misconfigured (missing field, bad value, etc.)."""
