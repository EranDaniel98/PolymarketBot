import pytest
from polymarket_bot.backtesting.engine import BacktestEngine


def test_winning_trade():
    engine = BacktestEngine(starting_balance=100.0, bootstrap_size_pct=0.10)
    pnl = engine.simulate_trade(0.40, 0.7, "Yes", "YES")
    assert pnl > 0  # Won: payout = size * (1 - 0.4) / 0.4 = 10 * 1.5 = 15
    assert engine.balance > 100.0


def test_losing_trade():
    engine = BacktestEngine(starting_balance=100.0, bootstrap_size_pct=0.10)
    pnl = engine.simulate_trade(0.40, 0.7, "No", "YES")
    assert pnl < -10.0  # Lost position + fees + slippage
    assert engine.balance < 90.0


def test_no_trade_below_minimum():
    # 50 * 0.10 = $5, which is below $10 minimum -> no trade
    engine = BacktestEngine(starting_balance=50.0, bootstrap_size_pct=0.10)
    pnl = engine.simulate_trade(0.40, 0.7, "Yes", "YES")
    assert pnl == 0.0


def test_results_calculation():
    engine = BacktestEngine(starting_balance=200.0, bootstrap_size_pct=0.10)
    engine.simulate_trade(0.40, 0.7, "Yes", "YES")  # Win
    engine.simulate_trade(0.60, 0.6, "No", "YES")   # Lose
    result = engine.get_results()
    assert result.total_trades == 2
    assert result.winning_trades == 1
    assert result.losing_trades == 1
    assert result.win_rate == 0.5


def test_no_direction():
    engine = BacktestEngine(starting_balance=200.0, bootstrap_size_pct=0.10)
    pnl = engine.simulate_trade(0.40, 0.7, "No", "NO")  # NO bet, outcome No = win
    assert pnl > 0


def test_empty_results():
    engine = BacktestEngine()
    result = engine.get_results()
    assert result.total_trades == 0
    assert result.win_rate == 0
    assert result.total_pnl == 0.0


def test_per_signal_tracking():
    engine = BacktestEngine(starting_balance=200.0, bootstrap_size_pct=0.10)
    engine.simulate_trade(0.40, 0.7, "Yes", "YES", source="favorite_longshot")
    engine.simulate_trade(0.60, 0.6, "No", "YES", source="divergence")
    result = engine.get_results()
    assert "favorite_longshot" in result.per_signal
    assert "divergence" in result.per_signal
    assert result.per_signal["favorite_longshot"]["wins"] == 1
    assert result.per_signal["divergence"]["losses"] == 1


@pytest.mark.asyncio
async def test_backtest_runs_multiple_signals():
    """Backtest should evaluate FLB + divergence + weather, not just FLB."""
    from polymarket_bot.backtesting.engine import build_offline_signals
    plugins = await build_offline_signals()
    assert len(plugins) >= 2  # At least FLB + one more
    names = [p.name for p in plugins]
    assert "favorite_longshot" in names
    # Clean up
    for p in plugins:
        await p.stop()
