"""CLI analytics — reads DB + log files to produce actionable reports."""

import json
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from polymarket_bot.database import Database

logger = logging.getLogger(__name__)
console = Console()


async def run_analytics(db_path: str = "polymarket_bot.db", log_path: str = "logs/bot.jsonl"):
    db = Database(Path(db_path))
    await db.initialize()

    try:
        console.print("\n[bold cyan]===  Polymarket Bot Analytics  ===[/]\n")

        await _print_performance(db)
        await _print_signal_accuracy(db)
        await _print_calibration(db)
        await _print_fee_impact(db)
        _print_log_summary(Path(log_path))

    finally:
        await db.close()


async def _print_performance(db: Database) -> None:
    total_pnl = await db.get_total_pnl()
    win_rate = await db.get_win_rate()
    trade_count = await db.get_trade_count()

    table = Table(title="Overall Performance", border_style="cyan")
    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")

    pnl_style = "green" if total_pnl >= 0 else "red"
    table.add_row("Total Trades", str(trade_count))
    table.add_row("Win Rate", f"{win_rate:.1%}")
    table.add_row("Total P&L", f"[{pnl_style}]${total_pnl:+.2f}[/]")
    console.print(table)
    console.print()


async def _print_signal_accuracy(db: Database) -> None:
    report = await db.get_accuracy_report()
    if not report:
        console.print("[dim]No signal accuracy data yet (markets need to resolve)[/]\n")
        return

    table = Table(title="Signal Accuracy (resolved markets only)", border_style="cyan")
    table.add_column("Signal", style="white")
    table.add_column("Signals", justify="right")
    table.add_column("Accuracy", justify="right")
    table.add_column("Avg Confidence", justify="right")
    table.add_column("Conf Gap", justify="right")

    for source, stats in sorted(report.items()):
        gap = stats["avg_confidence"] - stats["accuracy"] if stats["avg_confidence"] else 0
        gap_style = "red" if gap > 0.10 else "yellow" if gap > 0.05 else "green"
        table.add_row(
            source,
            str(stats["n_signals"]),
            f"{stats['accuracy']:.1%}",
            f"{stats['avg_confidence']:.1%}" if stats["avg_confidence"] else "N/A",
            f"[{gap_style}]{gap:+.1%}[/]",
        )
    console.print(table)
    console.print()


async def _print_calibration(db: Database) -> None:
    for source in ("llm", "favorite_longshot", "divergence", "weather"):
        gap = await db.get_confidence_gap(source)
        if not gap or gap["n_signals"] < 5:
            continue

        buckets = await db.get_confidence_calibration(source, bucket_size=0.20)
        if not buckets:
            continue

        table = Table(title=f"Calibration: {source} (n={gap['n_signals']})", border_style="magenta")
        table.add_column("Confidence Bucket", style="white")
        table.add_column("Signals", justify="right")
        table.add_column("Actual Accuracy", justify="right")
        table.add_column("Status", justify="right")

        for b in buckets:
            expected_mid = (b["bucket_min"] + b["bucket_max"]) / 2
            diff = b["accuracy"] - expected_mid
            if abs(diff) < 0.10:
                status = "[green]Well calibrated[/]"
            elif diff > 0:
                status = "[cyan]Underconfident[/]"
            else:
                status = "[red]Overconfident[/]"
            table.add_row(
                f"{b['bucket_min']:.0%}-{b['bucket_max']:.0%}",
                str(b["total"]),
                f"{b['accuracy']:.0%}",
                status,
            )
        console.print(table)

        gap_style = "red" if gap["gap"] > 0.10 else "yellow" if gap["gap"] > 0.05 else "green"
        console.print(
            f"  [{gap_style}]Confidence gap: {gap['gap']:+.1%}[/] "
            f"(predicted {gap['avg_confidence']:.0%} vs actual {gap['actual_accuracy']:.0%})\n"
        )


async def _print_fee_impact(db: Database) -> None:
    report = await db.get_fee_impact_report()
    if report["n_trades"] == 0:
        return

    table = Table(title="Fee Impact Analysis", border_style="yellow")
    table.add_column("Metric", style="white")
    table.add_column("Value", justify="right")

    table.add_row("Total Volume", f"${report['total_volume']:.2f}")
    table.add_row("Total Fees", f"[red]${report['total_fees']:.2f}[/]")
    table.add_row("Fees as % of Volume", f"{report['fee_pct_of_volume']:.2%}")
    table.add_row("Net P&L (after fees)", f"${report['total_pnl']:.2f}")

    if report["n_trades"] > 0:
        avg_trade = report["total_volume"] / report["n_trades"]
        min_profitable = report["total_fees"] / report["n_trades"] if report["n_trades"] > 0 else 0
        table.add_row("Avg Trade Size", f"${avg_trade:.2f}")
        table.add_row("Avg Fee per Trade", f"${min_profitable:.2f}")

    console.print(table)
    console.print()


def _print_log_summary(log_path: Path) -> None:
    if not log_path.exists():
        console.print("[dim]No log file found — run the bot with file logging enabled[/]\n")
        return

    counts = {}
    total = 0
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
            event_type = record.get("event_type", "other")
            counts[event_type] = counts.get(event_type, 0) + 1
            total += 1
        except json.JSONDecodeError:
            continue

    if not counts:
        console.print("[dim]Log file empty[/]\n")
        return

    table = Table(title=f"Log Events ({total} total)", border_style="green")
    table.add_column("Event Type", style="white")
    table.add_column("Count", justify="right")

    for event_type, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(event_type, str(count))
    console.print(table)
    console.print()
