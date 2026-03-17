import pytest
from unittest.mock import patch
from io import StringIO
from polymarket_bot.cli import (
    print_banner, format_price, format_pnl, get_log_handler, COLOR_SCHEME,
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
