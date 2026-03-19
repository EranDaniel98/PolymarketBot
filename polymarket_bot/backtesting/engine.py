"""Backtesting engine — replay historical markets through signal pipeline."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from polymarket_bot.backtesting.data_loader import HistoricalDataLoader
from polymarket_bot.models import Market, Direction
from polymarket_bot.signals.favorite_longshot import FavoriteLongshotSignal

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class BacktestResult:
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float
    max_drawdown: float
    win_rate: float
    avg_profit: float
    avg_loss: float
    final_balance: float
    per_signal: dict = field(default_factory=dict)


ROUND_TRIP_FEE = 0.02  # ~2% maker+taker fees
AVG_SLIPPAGE = 0.003   # ~0.3% average slippage


class BacktestEngine:
    def __init__(self, starting_balance: float = 309.0, bootstrap_size_pct: float = 0.04):
        self.balance = starting_balance
        self.starting_balance = starting_balance
        self.bootstrap_size_pct = bootstrap_size_pct
        self.trades: list[dict] = []
        self.peak_balance = starting_balance

    def simulate_trade(
        self, entry_price: float, confidence: float,
        actual_outcome: str, direction: str, source: str = "",
    ) -> float:
        size = self.balance * self.bootstrap_size_pct
        if size < 10 or size > self.balance:
            return 0.0

        won = (
            (direction == "YES" and actual_outcome == "Yes")
            or (direction == "NO" and actual_outcome == "No")
        )

        # Model realistic fees and slippage
        fee_cost = size * ROUND_TRIP_FEE
        slippage_cost = size * AVG_SLIPPAGE

        if won:
            pnl = size * ((1.0 - entry_price) / entry_price) - fee_cost - slippage_cost
        else:
            pnl = -size - fee_cost - slippage_cost

        self.balance += pnl
        self.peak_balance = max(self.peak_balance, self.balance)
        self.trades.append({
            "pnl": pnl, "won": won, "confidence": confidence,
            "entry_price": entry_price, "direction": direction,
            "source": source,
        })
        return pnl

    def get_results(self) -> BacktestResult:
        wins = [t for t in self.trades if t["won"]]
        losses = [t for t in self.trades if not t["won"]]

        max_dd = 0.0
        running_peak = self.starting_balance
        running_balance = self.starting_balance
        for t in self.trades:
            running_balance += t["pnl"]
            running_peak = max(running_peak, running_balance)
            dd = (running_peak - running_balance) / running_peak if running_peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Per-signal breakdown
        per_signal: dict[str, dict] = {}
        for t in self.trades:
            src = t.get("source", "unknown")
            if src not in per_signal:
                per_signal[src] = {"wins": 0, "losses": 0, "pnl": 0.0}
            per_signal[src]["pnl"] += t["pnl"]
            if t["won"]:
                per_signal[src]["wins"] += 1
            else:
                per_signal[src]["losses"] += 1

        return BacktestResult(
            total_trades=len(self.trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            total_pnl=self.balance - self.starting_balance,
            max_drawdown=max_dd,
            win_rate=len(wins) / len(self.trades) if self.trades else 0,
            avg_profit=sum(t["pnl"] for t in wins) / len(wins) if wins else 0,
            avg_loss=sum(t["pnl"] for t in losses) / len(losses) if losses else 0,
            final_balance=self.balance,
            per_signal=per_signal,
        )


def _print_results(result: BacktestResult) -> None:
    table = Table(title="Backtest Results", border_style="cyan")
    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")

    pnl_style = "green" if result.total_pnl >= 0 else "red"
    table.add_row("Total Trades", str(result.total_trades))
    table.add_row("Winning / Losing", f"{result.winning_trades} / {result.losing_trades}")
    table.add_row("Win Rate", f"{result.win_rate:.1%}")
    table.add_row("Total P&L", f"[{pnl_style}]${result.total_pnl:+.2f}[/]")
    table.add_row("Final Balance", f"${result.final_balance:.2f}")
    table.add_row("Max Drawdown", f"[red]{result.max_drawdown:.1%}[/]")
    table.add_row("Avg Profit", f"[green]${result.avg_profit:+.2f}[/]")
    table.add_row("Avg Loss", f"[red]${result.avg_loss:+.2f}[/]")
    console.print(table)

    # Per-signal breakdown
    if result.per_signal:
        sig_table = Table(title="Per-Signal Breakdown", border_style="cyan")
        sig_table.add_column("Signal", style="white")
        sig_table.add_column("Trades", justify="right")
        sig_table.add_column("Win Rate", justify="right")
        sig_table.add_column("P&L", justify="right")
        for src, stats in result.per_signal.items():
            total = stats["wins"] + stats["losses"]
            wr = stats["wins"] / total if total > 0 else 0
            pnl_s = "green" if stats["pnl"] >= 0 else "red"
            sig_table.add_row(
                src, str(total), f"{wr:.1%}",
                f"[{pnl_s}]${stats['pnl']:+.2f}[/]",
            )
        console.print(sig_table)


def _parse_to_market(raw: dict) -> Market | None:
    """Convert raw Gamma API data to a Market object for signal evaluation."""
    prices = raw.get("outcome_prices", [])
    if not prices:
        return None
    try:
        yes_price = float(prices[0])
    except (ValueError, IndexError):
        return None
    if yes_price <= 0.01 or yes_price >= 0.99:
        return None

    end_str = raw.get("end_date", "")
    try:
        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        end_date = datetime.now(timezone.utc)

    return Market(
        id=raw.get("condition_id", ""),
        question=raw.get("question", ""),
        end_date=end_date,
        tokens={},
        current_price=yes_price,
        category=raw.get("category", ""),
        volume=float(raw.get("volume", 0) or 0),
    )


async def run_backtest(days: int = 30, balance: float = 309.0) -> BacktestResult:
    console.print(f"\n[bold cyan]Running signal-based backtest — last {days} days, ${balance:.0f} starting balance[/]\n")

    loader = HistoricalDataLoader()
    raw_markets = await loader.fetch_resolved_markets(limit=min(days * 5, 500))

    if not raw_markets:
        console.print("[bold red]No resolved markets found for backtesting[/]")
        return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, balance)

    console.print(f"[green]Loaded {len(raw_markets)} resolved markets[/]")

    # Initialize signal plugins that don't need live APIs
    flb = FavoriteLongshotSignal()
    await flb.start()

    engine = BacktestEngine(starting_balance=balance)

    for raw in raw_markets:
        outcome = raw.get("outcome", "")
        if not outcome:
            continue

        market = _parse_to_market(raw)
        if not market:
            continue

        # Evaluate FLB signal
        signals = []
        if flb.can_evaluate(market):
            sig = await flb.evaluate(market)
            if sig:
                signals.append(sig)

        # Execute best signal
        if signals:
            best = max(signals, key=lambda s: s.confidence)
            if best.confidence >= 0.15:
                entry_price = market.current_price if best.direction == Direction.YES else 1.0 - market.current_price
                engine.simulate_trade(
                    entry_price, best.confidence, outcome,
                    best.direction.value, best.source,
                )

    await flb.stop()

    result = engine.get_results()
    _print_results(result)
    return result
