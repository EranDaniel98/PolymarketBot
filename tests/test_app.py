def test_app_module_imports():
    """Verify app.py can be imported without errors."""
    from polymarket_weather import app
    assert hasattr(app, "run_bot")


def test_cli_module_imports():
    from polymarket_weather import cli
    assert hasattr(cli, "print_banner")
    assert hasattr(cli, "format_pnl")
    assert hasattr(cli, "build_status_line")


def test_format_pnl():
    from polymarket_weather.cli import format_pnl
    assert format_pnl(25.50) == "+$25.50"
    assert format_pnl(-10.00) == "-$10.00"
    assert format_pnl(0) == "+$0.00"


def test_format_price():
    from polymarket_weather.cli import format_price
    assert format_price(0.5500) == "$0.5500"


def test_status_line():
    from polymarket_weather.cli import build_status_line
    line = build_status_line(1000, 25.50, 150.0, 3, 75.0, True, 3661)
    assert "[PAPER]" in line
    assert "$1000.00" in line
    assert "1h1m" in line
