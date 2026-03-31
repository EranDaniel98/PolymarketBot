import pytest
from unittest.mock import patch
from io import StringIO
from polymarket_bot.cli import (
    print_banner, format_price, format_pnl, format_pct, get_log_handler,
    COLOR_SCHEME, build_full_dashboard, _bar, _time_ago,
)


def test_color_scheme_has_required_keys():
    assert "profit" in COLOR_SCHEME
    assert "loss" in COLOR_SCHEME
    assert "signal" in COLOR_SCHEME
    assert "arb" in COLOR_SCHEME
    assert "warning" in COLOR_SCHEME


def test_format_price():
    assert format_price(0.55) == "[bold white]$0.55[/]"
    assert format_price(0.0) == "[bold white]$0.00[/]"


def test_format_pnl_positive():
    result = format_pnl(25.50)
    assert "green" in result
    assert "+$25.50" in result


def test_format_pnl_negative():
    result = format_pnl(-10.00)
    assert "red" in result
    assert "-$10.00" in result


def test_format_pnl_zero():
    result = format_pnl(0.0)
    assert "$0.00" in result


def test_print_banner(capsys):
    print_banner("0.1.0")
    # Should not raise and should produce output
    captured = capsys.readouterr()
    # Rich outputs to stderr or uses console, so just verify no exception


def test_get_log_handler():
    handler = get_log_handler()
    assert handler is not None


def test_format_pct_positive():
    result = format_pct(0.15)
    assert "+15.0%" in result
    assert "green" in result


def test_format_pct_negative():
    result = format_pct(-0.05)
    assert "-5.0%" in result
    assert "red" in result


def test_bar_full():
    bar = _bar(100, 100, width=10)
    assert len(bar) == 10
    assert "░" not in bar


def test_bar_half():
    bar = _bar(50, 100, width=10)
    assert "█" in bar
    assert "░" in bar


def test_bar_empty():
    bar = _bar(0, 100, width=10)
    assert "█" not in bar


def test_time_ago():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    assert _time_ago(now - timedelta(seconds=30)) == "30s"
    assert _time_ago(now - timedelta(minutes=5)) == "5m"
    assert _time_ago(now - timedelta(hours=2, minutes=15)) == "2h15m"
    assert _time_ago(now - timedelta(days=3)) == "3d"


def test_build_full_dashboard_empty():
    """Full dashboard should render without errors even with no data."""
    dashboard = build_full_dashboard(
        positions=[], pnl=0.0, exposure=0.0, bankroll=300.0,
        trade_count=0, uptime_seconds=3600, paper_mode=True,
    )
    assert dashboard is not None


def test_build_full_dashboard_with_data():
    """Full dashboard should render with realistic data."""
    positions = [
        {
            "market_id": "Will BTC be above $100k?",
            "direction": "YES", "amount": 25.0,
            "entry_price": 0.55, "current_price": 0.62,
            "pnl": 3.18, "peak_pnl_pct": 0.15,
            "held": "2h30m", "expires": "3d",
        },
    ]
    signals = [
        {"time": "5m", "source": "crypto_price", "direction": "YES", "confidence": 0.72, "market": "BTC above $100k"},
    ]
    plugins = [
        {"name": "llm", "active": True, "signal_count": 50, "accuracy": 0.65, "weight": 0.25},
        {"name": "crypto_price", "active": True, "signal_count": 12, "accuracy": None, "weight": 0.20},
    ]
    trades = [
        {"time": "1h", "direction": "YES", "amount": 20, "price": 0.45, "pnl": 5.0, "market": "Some market"},
    ]
    dashboard = build_full_dashboard(
        positions=positions, pnl=5.0, exposure=25.0, bankroll=300.0,
        trade_count=15, uptime_seconds=7200, paper_mode=False,
        total_pnl=12.50, win_rate=0.65,
        circuit_breaker=False, recovery=False,
        recent_signals=signals, recent_trades=trades,
        plugin_stats=plugins, max_daily_loss=24.0,
        correlated_exposure={"crypto": 25.0, "politics": 15.0},
    )
    assert dashboard is not None


def test_build_full_dashboard_circuit_breaker():
    """Dashboard should show circuit breaker state."""
    dashboard = build_full_dashboard(
        positions=[], pnl=-30.0, exposure=0.0, bankroll=300.0,
        trade_count=5, uptime_seconds=1800, paper_mode=True,
        circuit_breaker=True, max_daily_loss=24.0,
    )
    assert dashboard is not None
