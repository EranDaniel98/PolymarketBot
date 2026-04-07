"""Tests for the polymarket_weather.errors hierarchy (Phase 5.2)."""

import pytest

from polymarket_weather.errors import (
    ConfigError,
    DataError,
    ForecastUnavailableError,
    InsufficientEdgeError,
    InvalidMarketError,
    PolymarketWeatherError,
    RiskLimitError,
    StaleDataError,
    TradingError,
)


def test_all_inherit_from_base():
    for cls in (
        TradingError, InsufficientEdgeError, RiskLimitError, InvalidMarketError,
        DataError, StaleDataError, ForecastUnavailableError, ConfigError,
    ):
        assert issubclass(cls, PolymarketWeatherError)


def test_trading_subclasses_inherit_from_trading_error():
    for cls in (InsufficientEdgeError, RiskLimitError, InvalidMarketError):
        assert issubclass(cls, TradingError)


def test_data_subclasses_inherit_from_data_error():
    for cls in (StaleDataError, ForecastUnavailableError):
        assert issubclass(cls, DataError)


def test_risk_limit_carries_reason_and_limit():
    err = RiskLimitError("max position breached", limit="max_position_usdc")
    assert err.reason == "max position breached"
    assert err.limit == "max_position_usdc"
    assert "max position breached" in str(err)


def test_stale_data_carries_source_and_age():
    err = StaleDataError("KJFK", age_seconds=4500)
    assert err.source == "KJFK"
    assert err.age_seconds == 4500
    assert "KJFK" in str(err)
    assert "4500" in str(err)


def test_can_catch_with_base_class():
    with pytest.raises(PolymarketWeatherError):
        raise InsufficientEdgeError("edge 0.02 < min 0.05")

    with pytest.raises(TradingError):
        raise RiskLimitError("max_open", limit="max_open")

    with pytest.raises(DataError):
        raise StaleDataError("KSFO", age_seconds=99999)
