import pytest
from datetime import datetime, timezone
from polymarket_weather.trading.positions import PositionManager, TrackedPosition


def test_track_entry():
    pm = PositionManager()
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    assert "0xabc" in pm.positions
    assert pm.positions["0xabc"].direction == "YES"


def test_track_exit():
    pm = PositionManager()
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    pm.track_exit("0xabc")
    assert "0xabc" not in pm.positions


def test_open_count_and_exposure():
    pm = PositionManager()
    pm.track_entry("0x1", "YES", 0.50, 20.0, "nyc", "e1")
    pm.track_entry("0x2", "NO", 0.60, 30.0, "la", "e2")
    assert pm.open_count == 2
    assert pm.total_exposure == 50.0


def test_pnl_yes_profit():
    pos = TrackedPosition("0x1", "YES", 0.55, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_pnl(current_price=0.75)
    assert pnl > 0


def test_pnl_yes_loss():
    pos = TrackedPosition("0x1", "YES", 0.55, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_pnl(current_price=0.40)
    assert pnl < 0


def test_pnl_no_profit():
    pos = TrackedPosition("0x1", "NO", 0.60, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_pnl(current_price=0.40)
    assert pnl > 0


def test_settlement_yes_wins():
    pos = TrackedPosition("0x1", "YES", 0.55, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_settlement_pnl(outcome="YES", fee=0.01)
    assert pnl > 0


def test_settlement_yes_loses():
    pos = TrackedPosition("0x1", "YES", 0.55, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_settlement_pnl(outcome="NO", fee=0.01)
    assert pnl < 0


def test_settlement_no_wins():
    pos = TrackedPosition("0x1", "NO", 0.40, 25.0, "nyc", "evt_1",
                          datetime.now(timezone.utc))
    pnl = pos.compute_settlement_pnl(outcome="NO", fee=0.01)
    assert pnl > 0


def test_exit_edge_inversion():
    pm = PositionManager(edge_inversion_threshold=-0.05)
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    should, reason = pm.check_exit("0xabc", current_price=0.55, current_edge=-0.10)
    assert should is True
    assert "edge_inversion" in reason


def test_no_exit_positive_edge():
    pm = PositionManager(edge_inversion_threshold=-0.05)
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    should, reason = pm.check_exit("0xabc", current_price=0.60, current_edge=0.10)
    assert should is False


def test_update_peak():
    pm = PositionManager()
    pm.track_entry("0x1", "YES", 0.50, 20.0, "nyc", "e1")
    pm.update_peak("0x1", 0.60)  # +20%
    assert pm.positions["0x1"].peak_pnl_pct > 0
    pm.update_peak("0x1", 0.55)  # Lower — peak should not decrease
    assert pm.positions["0x1"].peak_pnl_pct == pytest.approx(0.20, abs=0.01)


def test_get_position():
    pm = PositionManager()
    pm.track_entry("0x1", "YES", 0.50, 20.0, "nyc", "e1")
    assert pm.get_position("0x1") is not None
    assert pm.get_position("0x999") is None
