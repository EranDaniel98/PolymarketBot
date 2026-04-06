"""Rich CLI dashboard for the weather arbitrage bot."""

import time
from datetime import datetime, timezone


def format_price(price: float) -> str:
    return f"${price:.4f}"


def format_pnl(pnl: float) -> str:
    if pnl >= 0:
        return f"+${pnl:.2f}"
    return f"-${abs(pnl):.2f}"


def format_pct(pct: float) -> str:
    return f"{pct:.0%}"


def time_ago(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def print_banner(version: str) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        banner = (
            f"[bold cyan]Polymarket Weather Arbitrage Bot[/] v{version}\n"
            "[dim]METAR + NWP Ensemble vs Prediction Market Prices[/]"
        )
        console.print(Panel(banner, border_style="blue"))
    except ImportError:
        print(f"Polymarket Weather Arbitrage Bot v{version}")


def build_status_line(
    bankroll: float,
    daily_pnl: float,
    total_pnl: float,
    open_positions: int,
    total_exposure: float,
    paper_mode: bool,
    uptime_seconds: float,
) -> str:
    mode = "[PAPER]" if paper_mode else "[LIVE]"
    hours = int(uptime_seconds // 3600)
    mins = int((uptime_seconds % 3600) // 60)
    return (
        f"{mode} Bankroll: ${bankroll:.2f} | "
        f"Daily: {format_pnl(daily_pnl)} | "
        f"Total: {format_pnl(total_pnl)} | "
        f"Positions: {open_positions} (${total_exposure:.0f}) | "
        f"Uptime: {hours}h{mins}m"
    )
