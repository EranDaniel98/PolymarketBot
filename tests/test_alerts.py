from polymarket_weather.alerts.telegram import (
    WeatherTelegramNotifier, format_opportunity_message,
    format_trade_message, format_settlement_message,
    format_stale_station_message, format_daily_report, AlertLevel,
)


def test_notifier_init():
    n = WeatherTelegramNotifier(bot_token="test", chat_id="123")
    assert n.name == "telegram"


def test_format_opportunity():
    msg = format_opportunity_message("NYC", "Will NYC high be 50-54F?",
                                     our_p=0.75, market_p=0.55, edge=0.20, source="metar")
    assert "NYC" in msg
    assert "0.20" in msg


def test_format_trade():
    msg = format_trade_message("0xabc", "YES", 25.0, 0.55, "NYC temp 50-54F")
    assert "YES" in msg
    assert "$25.00" in msg


def test_format_settlement_win():
    msg = format_settlement_message("NYC temp", "YES", 15.50)
    assert "+$15.50" in msg


def test_format_settlement_loss():
    msg = format_settlement_message("NYC temp", "NO", -10.00)
    assert "-$10.00" in msg


def test_format_stale():
    msg = format_stale_station_message("KJFK", 4.5)
    assert "KJFK" in msg
    assert "4.5" in msg


def test_format_daily_report():
    stats = {"daily_pnl": 25.50, "total_pnl": 150.00, "trade_count": 5,
             "win_rate": 0.73, "open_positions": 3, "bankroll": 1150.00}
    msg = format_daily_report(stats)
    assert "$25.50" in msg
    assert "73%" in msg


def test_alert_on_filtering():
    n = WeatherTelegramNotifier("tok", "123", alert_on={"opportunity_found": False})
    assert n._alert_on.get("opportunity_found") is False
