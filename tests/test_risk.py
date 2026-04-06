import pytest
from polymarket_weather.trading.risk import RiskManager, RiskCheck


def test_risk_check_passes():
    rm = RiskManager()
    check = rm.check_trade(size_usdc=25.0, city="new york", region="northeast_us", market_id="0xabc")
    assert check.approved is True
    assert check.reason == ""


def test_risk_rejects_over_max_position():
    rm = RiskManager(max_position_usdc=50)
    check = rm.check_trade(size_usdc=60.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "max_position" in check.reason


def test_risk_rejects_below_minimum():
    rm = RiskManager(min_trade_size_usdc=5)
    check = rm.check_trade(size_usdc=3.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "below_minimum" in check.reason


def test_risk_rejects_duplicate_market():
    rm = RiskManager()
    rm.record_entry("0xabc", "nyc", "ne", 25.0)
    check = rm.check_trade(size_usdc=25.0, city="nyc", region="ne", market_id="0xabc")
    assert check.approved is False
    assert "duplicate" in check.reason


def test_risk_rejects_max_open_positions():
    rm = RiskManager(max_open_positions=2, max_total_exposure_usdc=10000)
    rm.record_entry("0x1", "nyc", "ne", 10.0)
    rm.record_entry("0x2", "la", "sw", 10.0)
    check = rm.check_trade(size_usdc=10.0, city="chi", region="mw", market_id="0x3")
    assert check.approved is False
    assert "max_open_positions" in check.reason


def test_risk_total_exposure_limit():
    rm = RiskManager(max_total_exposure_usdc=100)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    rm.record_entry("0x2", "la", "sw", 40.0)
    check = rm.check_trade(size_usdc=20.0, city="chi", region="mw", market_id="0x3")
    assert check.approved is False
    assert "total_exposure" in check.reason


def test_risk_city_exposure_limit():
    rm = RiskManager(max_exposure_per_city_usdc=60)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    check = rm.check_trade(size_usdc=20.0, city="nyc", region="ne", market_id="0x2")
    assert check.approved is False
    assert "city_exposure" in check.reason


def test_risk_region_exposure_limit():
    rm = RiskManager(max_exposure_per_region_usdc=80)
    rm.record_entry("0x1", "nyc", "ne", 50.0)
    rm.record_entry("0x2", "boston", "ne", 20.0)
    check = rm.check_trade(size_usdc=20.0, city="philly", region="ne", market_id="0x3")
    assert check.approved is False
    assert "region_exposure" in check.reason


def test_risk_daily_loss_cap():
    rm = RiskManager(daily_loss_cap_usdc=50)
    rm.record_daily_loss(50.0)
    check = rm.check_trade(size_usdc=10.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "daily_loss" in check.reason


def test_risk_trading_paused():
    rm = RiskManager()
    rm.pause()
    check = rm.check_trade(size_usdc=10.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is False
    assert "paused" in check.reason
    rm.resume()
    check2 = rm.check_trade(size_usdc=10.0, city="nyc", region="ne", market_id="0x1")
    assert check2.approved is True


def test_bootstrap_sizing():
    rm = RiskManager(max_position_usdc=50, bootstrap_trades=50, bootstrap_size_usdc=10)
    assert rm.get_max_size() == 10.0
    for _ in range(50):
        rm.record_completed_trade()
    assert rm.get_max_size() == 50.0


def test_record_entry_and_exit():
    rm = RiskManager()
    rm.record_entry("0xabc", "nyc", "ne", 25.0)
    assert rm.total_exposure == 25.0
    assert rm.open_count == 1
    rm.record_exit("0xabc")
    assert rm.total_exposure == 0.0
    assert rm.open_count == 0


def test_reset_daily():
    rm = RiskManager(daily_loss_cap_usdc=50)
    rm.record_daily_loss(50.0)
    rm.reset_daily()
    check = rm.check_trade(size_usdc=10.0, city="nyc", region="ne", market_id="0x1")
    assert check.approved is True
