import logging
from datetime import datetime, timezone
from rich.console import Console, Group
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.columns import Columns

console = Console()

COLOR_SCHEME = {
    "profit": "bold green",
    "loss": "bold red",
    "warning": "bold yellow",
    "signal": "bold cyan",
    "arb": "bold magenta",
    "info": "bold blue",
    "muted": "dim white",
    "header": "bold white on dark_blue",
}

BANNER = r"""
[bold cyan]  ____       _       __  __            _        _   ____        _   [/]
[bold cyan] |  _ \ ___ | |_   _|  \/  | __ _ _ __| | _____| |_| __ )  ___ | |_ [/]
[bold cyan] | |_) / _ \| | | | | |\/| |/ _` | '__| |/ / _ \ __|  _ \ / _ \| __|[/]
[bold cyan] |  __/ (_) | | |_| | |  | | (_| | |  |   <  __/ |_| |_) | (_) | |_ [/]
[bold cyan] |_|   \___/|_|\__, |_|  |_|\__,_|_|  |_|\_\___|\__|____/ \___/ \__|[/]
[bold cyan]               |___/                                                 [/]
"""


def print_banner(version: str) -> None:
    console.print(BANNER)
    console.print(
        Panel(
            f"[bold white]v{version}[/] | [cyan]Signal-Based Trading[/] | [magenta]Arbitrage Detection[/]",
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


def format_price(price: float) -> str:
    return f"[bold white]${price:.2f}[/]"


def format_pnl(pnl: float) -> str:
    if pnl > 0:
        return f"[{COLOR_SCHEME['profit']}]+${pnl:.2f}[/]"
    elif pnl < 0:
        return f"[{COLOR_SCHEME['loss']}]-${abs(pnl):.2f}[/]"
    return f"[{COLOR_SCHEME['muted']}]$0.00[/]"


def format_pct(value: float, invert: bool = False) -> str:
    """Format a percentage with color. invert=True means negative is good."""
    if value > 0:
        color = COLOR_SCHEME["loss" if invert else "profit"]
        return f"[{color}]+{value:.1%}[/]"
    elif value < 0:
        color = COLOR_SCHEME["profit" if invert else "loss"]
        return f"[{color}]{value:.1%}[/]"
    return f"[{COLOR_SCHEME['muted']}]0.0%[/]"


def format_confidence(confidence: float) -> str:
    if confidence >= 0.8:
        return f"[{COLOR_SCHEME['profit']}]{confidence:.0%}[/]"
    elif confidence >= 0.5:
        return f"[{COLOR_SCHEME['warning']}]{confidence:.0%}[/]"
    return f"[{COLOR_SCHEME['muted']}]{confidence:.0%}[/]"


def format_signal_source(source: str) -> str:
    return f"[{COLOR_SCHEME['signal']}]{source}[/]"


def format_arb(spread: float) -> str:
    return f"[{COLOR_SCHEME['arb']}]{spread:.1%} spread[/]"


def _time_ago(dt: datetime) -> str:
    """Compact human-readable time since a datetime."""
    delta = datetime.now(timezone.utc) - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    if total_seconds < 86400:
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        return f"{h}h{m}m"
    return f"{total_seconds // 86400}d"


def _bar(value: float, max_value: float, width: int = 20, filled: str = "█", empty: str = "░") -> str:
    """ASCII progress bar."""
    if max_value <= 0:
        return empty * width
    ratio = min(value / max_value, 1.0)
    filled_count = int(ratio * width)
    return filled * filled_count + empty * (width - filled_count)


# --- Event output functions ---


def print_trade_execution(market_id: str, direction: str, amount: float, price: float,
                          question: str = "") -> None:
    color = COLOR_SCHEME["profit"] if direction == "YES" else COLOR_SCHEME["loss"]
    market_label = question or market_id
    console.print(
        Panel(
            f"[{color}]{direction}[/] {format_price(price)} x ${amount:.2f}\n"
            f"[{COLOR_SCHEME['muted']}]{market_label}[/]",
            title="[bold]Trade Executed[/]",
            border_style="green",
        )
    )


def print_signal(source: str, market_id: str, direction: str, confidence: float) -> None:
    console.print(
        f"  [{COLOR_SCHEME['signal']}]SIGNAL[/] "
        f"{format_signal_source(source)} -> {direction} "
        f"{format_confidence(confidence)} "
        f"[{COLOR_SCHEME['muted']}]{market_id}[/]"
    )


def print_arb_opportunity(platforms: list[str], spread: float, profit: float) -> None:
    console.print(
        Panel(
            f"{format_arb(spread)} across {', '.join(platforms)}\n"
            f"Est. profit: {format_pnl(profit)}",
            title=f"[{COLOR_SCHEME['arb']}]Arbitrage Opportunity[/]",
            border_style="magenta",
        )
    )


def print_circuit_breaker(daily_loss: float, limit: float) -> None:
    console.print(
        Panel(
            f"[{COLOR_SCHEME['loss']}]Daily loss {format_pnl(daily_loss)} hit limit ${limit:.2f}[/]\n"
            f"[{COLOR_SCHEME['warning']}]ALL TRADING HALTED[/]",
            title=f"[{COLOR_SCHEME['loss']}]CIRCUIT BREAKER[/]",
            border_style="red",
        )
    )


# --- Dashboard panels ---


def _build_status_bar(
    bankroll: float, daily_pnl: float, total_pnl: float, exposure: float,
    trade_count: int, win_rate: float, uptime_seconds: float, paper_mode: bool,
    circuit_breaker: bool = False,
) -> Panel:
    """Top status bar with key metrics."""
    mode = "[bold yellow]● PAPER[/]" if paper_mode else "[bold green]● LIVE[/]"
    hours = int(uptime_seconds // 3600)
    mins = int((uptime_seconds % 3600) // 60)

    cb_status = f"  [bold red]⚠ CIRCUIT BREAKER[/]" if circuit_breaker else ""

    exposure_pct = exposure / bankroll if bankroll > 0 else 0
    exposure_color = "red" if exposure_pct > 0.8 else "yellow" if exposure_pct > 0.5 else "green"

    text = (
        f" {mode}{cb_status}  │  "
        f"[bold]Bankroll:[/] ${bankroll:.2f}  │  "
        f"[bold]Daily:[/] {format_pnl(daily_pnl)}  │  "
        f"[bold]Total:[/] {format_pnl(total_pnl)}  │  "
        f"[bold]Win Rate:[/] {format_confidence(win_rate)}  │  "
        f"[bold]Exposure:[/] [{exposure_color}]${exposure:.0f}/{bankroll * 0.6:.0f}[/]  │  "
        f"[bold]Trades:[/] {trade_count}  │  "
        f"[bold]Uptime:[/] {hours}h{mins:02d}m"
    )
    return Panel(text, border_style="cyan", padding=(0, 0))


def _build_positions_table(positions: list[dict]) -> Panel:
    """Open positions table with detailed metrics."""
    table = Table(
        border_style="cyan", expand=True, show_edge=False, pad_edge=False,
        row_styles=["", "dim"],
    )
    table.add_column("Market", style="white", max_width=45, no_wrap=True)
    table.add_column("Side", justify="center", width=5)
    table.add_column("Size", justify="right", width=8)
    table.add_column("Entry", justify="right", width=7)
    table.add_column("Now", justify="right", width=7)
    table.add_column("P&L", justify="right", width=10)
    table.add_column("P&L%", justify="right", width=7)
    table.add_column("Peak", justify="right", width=7)
    table.add_column("Held", justify="right", width=6)
    table.add_column("Expires", justify="right", width=8)

    if not positions:
        table.add_row(
            f"[{COLOR_SCHEME['muted']}]No open positions[/]",
            "", "", "", "", "", "", "", "", "",
        )
    else:
        total_exposure = 0
        total_pnl = 0
        for pos in positions:
            side_color = COLOR_SCHEME["profit"] if pos.get("direction") == "YES" else COLOR_SCHEME["loss"]
            pnl = pos.get("pnl", 0)
            amount = pos.get("amount", 0)
            entry = pos.get("entry_price", 0)
            pnl_pct = pnl / amount if amount > 0 else 0
            peak_pct = pos.get("peak_pnl_pct", 0)
            held = pos.get("held", "")
            expires = pos.get("expires", "")

            total_exposure += amount
            total_pnl += pnl

            table.add_row(
                pos.get("market_id", "")[:45],
                f"[{side_color}]{pos.get('direction', '')}[/]",
                f"${amount:.0f}",
                f"${entry:.2f}",
                f"${pos.get('current_price', 0):.2f}",
                format_pnl(pnl),
                format_pct(pnl_pct),
                f"[{COLOR_SCHEME['profit']}]{peak_pct:.0%}[/]" if peak_pct > 0 else f"[{COLOR_SCHEME['muted']}]—[/]",
                f"[{COLOR_SCHEME['muted']}]{held}[/]",
                f"[{COLOR_SCHEME['muted']}]{expires}[/]",
            )

        table.add_section()
        table.add_row(
            f"[bold]{len(positions)} position{'s' if len(positions) != 1 else ''}[/]",
            "", f"[bold]${total_exposure:.0f}[/]", "", "",
            f"[bold]{format_pnl(total_pnl)}[/]",
            "", "", "", "",
        )

    return Panel(table, title="[bold cyan]Open Positions[/]", border_style="cyan", padding=(0, 0))


def _build_signals_panel(recent_signals: list[dict]) -> Panel:
    """Recent signals panel."""
    table = Table(
        border_style="blue", expand=True, show_edge=False, pad_edge=False,
        show_header=True,
    )
    table.add_column("Time", width=5, style=COLOR_SCHEME["muted"])
    table.add_column("Source", width=14)
    table.add_column("Dir", width=4, justify="center")
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Market", max_width=35, no_wrap=True)

    if not recent_signals:
        table.add_row(f"[{COLOR_SCHEME['muted']}]Waiting for signals...[/]", "", "", "", "")
    else:
        for sig in recent_signals[-8:]:  # Show last 8
            dir_color = COLOR_SCHEME["profit"] if sig.get("direction") == "YES" else COLOR_SCHEME["loss"]
            table.add_row(
                sig.get("time", ""),
                format_signal_source(sig.get("source", "")),
                f"[{dir_color}]{sig.get('direction', '')}[/]",
                format_confidence(sig.get("confidence", 0)),
                f"[{COLOR_SCHEME['muted']}]{sig.get('market', '')[:35]}[/]",
            )

    return Panel(table, title="[bold blue]Recent Signals[/]", border_style="blue", padding=(0, 0))


def _build_plugin_status_panel(plugin_stats: list[dict]) -> Panel:
    """Signal plugin health/status panel."""
    table = Table(
        border_style="green", expand=True, show_edge=False, pad_edge=False,
    )
    table.add_column("Plugin", width=16)
    table.add_column("Status", width=6, justify="center")
    table.add_column("Signals", width=7, justify="right")
    table.add_column("Accuracy", width=8, justify="right")
    table.add_column("Weight", width=6, justify="right")

    for plugin in plugin_stats:
        status = "[bold green]●[/]" if plugin.get("active") else "[bold red]●[/]"
        acc = plugin.get("accuracy")
        acc_str = format_confidence(acc) if acc is not None else f"[{COLOR_SCHEME['muted']}]—[/]"
        table.add_row(
            format_signal_source(plugin.get("name", "")),
            status,
            str(plugin.get("signal_count", 0)),
            acc_str,
            f"{plugin.get('weight', 0):.0%}",
        )

    return Panel(table, title="[bold green]Signal Plugins[/]", border_style="green", padding=(0, 0))


def _build_recent_trades_panel(trades: list[dict]) -> Panel:
    """Recent trades panel."""
    table = Table(
        border_style="yellow", expand=True, show_edge=False, pad_edge=False,
    )
    table.add_column("Time", width=5, style=COLOR_SCHEME["muted"])
    table.add_column("Dir", width=4, justify="center")
    table.add_column("Size", width=7, justify="right")
    table.add_column("Price", width=7, justify="right")
    table.add_column("P&L", width=8, justify="right")
    table.add_column("Market", max_width=30, no_wrap=True)

    if not trades:
        table.add_row(f"[{COLOR_SCHEME['muted']}]No trades yet[/]", "", "", "", "", "")
    else:
        for trade in trades[-6:]:  # Show last 6
            dir_color = COLOR_SCHEME["profit"] if trade.get("direction") == "YES" else COLOR_SCHEME["loss"]
            table.add_row(
                trade.get("time", ""),
                f"[{dir_color}]{trade.get('direction', '')}[/]",
                f"${trade.get('amount', 0):.0f}",
                f"${trade.get('price', 0):.2f}",
                format_pnl(trade.get("pnl", 0)),
                f"[{COLOR_SCHEME['muted']}]{trade.get('market', '')[:30]}[/]",
            )

    return Panel(table, title="[bold yellow]Recent Trades[/]", border_style="yellow", padding=(0, 0))


def _build_risk_panel(
    exposure: float, bankroll: float, daily_pnl: float,
    max_daily_loss: float, circuit_breaker: bool, recovery: bool,
    correlated: dict[str, float] | None = None,
) -> Panel:
    """Risk management status panel."""
    lines = []

    # Exposure bar
    max_exp = bankroll * 0.6
    exp_pct = exposure / max_exp if max_exp > 0 else 0
    exp_color = "red" if exp_pct > 0.8 else "yellow" if exp_pct > 0.5 else "green"
    bar = _bar(exposure, max_exp, width=15)
    lines.append(f"[bold]Exposure:[/]  [{exp_color}]{bar}[/] ${exposure:.0f}/${max_exp:.0f}")

    # Daily P&L bar (loss toward circuit breaker)
    loss_ratio = abs(min(daily_pnl, 0)) / max_daily_loss if max_daily_loss > 0 else 0
    loss_color = "red" if loss_ratio > 0.7 else "yellow" if loss_ratio > 0.4 else "green"
    loss_bar = _bar(abs(min(daily_pnl, 0)), max_daily_loss, width=15)
    lines.append(f"[bold]Loss Lim:[/]  [{loss_color}]{loss_bar}[/] {format_pnl(daily_pnl)}/{format_pnl(-max_daily_loss)}")

    # Status indicators
    statuses = []
    if circuit_breaker:
        statuses.append("[bold red]⚠ HALTED[/]")
    elif recovery:
        statuses.append("[bold yellow]↻ RECOVERY[/]")
    else:
        statuses.append("[bold green]✓ ACTIVE[/]")

    lines.append(f"[bold]Status:[/]    {' '.join(statuses)}")

    # Top correlated categories
    if correlated:
        top = sorted(correlated.items(), key=lambda x: x[1], reverse=True)[:3]
        cats = "  ".join(f"{cat}: ${amt:.0f}" for cat, amt in top if amt > 0)
        if cats:
            lines.append(f"[bold]Categories:[/] [{COLOR_SCHEME['muted']}]{cats}[/]")

    return Panel(
        "\n".join(lines),
        title="[bold red]Risk Management[/]" if circuit_breaker else "[bold green]Risk Management[/]",
        border_style="red" if circuit_breaker else "green",
        padding=(0, 1),
    )


def build_full_dashboard(
    positions: list[dict], pnl: float, exposure: float, bankroll: float,
    trade_count: int, uptime_seconds: float, paper_mode: bool,
    total_pnl: float = 0.0,
    win_rate: float = 0.0,
    circuit_breaker: bool = False,
    recovery: bool = False,
    recent_signals: list[dict] | None = None,
    recent_trades: list[dict] | None = None,
    plugin_stats: list[dict] | None = None,
    max_daily_loss: float = 0.0,
    correlated_exposure: dict[str, float] | None = None,
) -> Group:
    """Build the full multi-panel CLI dashboard."""

    # Status bar (top)
    status = _build_status_bar(
        bankroll, pnl, total_pnl, exposure, trade_count, win_rate,
        uptime_seconds, paper_mode, circuit_breaker,
    )

    # Positions table (main content)
    positions_panel = _build_positions_table(positions)

    # Bottom row: signals + plugins + risk (side by side)
    signals = _build_signals_panel(recent_signals or [])
    plugins = _build_plugin_status_panel(plugin_stats or [])
    trades = _build_recent_trades_panel(recent_trades or [])
    risk = _build_risk_panel(
        exposure, bankroll, pnl, max_daily_loss,
        circuit_breaker, recovery, correlated_exposure,
    )

    # Use Columns for side-by-side layout
    bottom_left = Group(signals, trades)
    bottom_right = Group(plugins, risk)

    return Group(
        status,
        positions_panel,
        Columns([bottom_left, bottom_right], expand=True, equal=True),
    )


# Legacy compatibility — simple table version
def build_dashboard_table(positions: list[dict], pnl: float, exposure: float, bankroll: float) -> Table:
    table = Table(title="[bold cyan]Portfolio Dashboard[/]", border_style="cyan")
    table.add_column("Market", style="white", max_width=40)
    table.add_column("Side", justify="center")
    table.add_column("Amount", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")

    for pos in positions:
        side_color = COLOR_SCHEME["profit"] if pos.get("direction") == "YES" else COLOR_SCHEME["loss"]
        table.add_row(
            pos.get("market_id", "")[:40],
            f"[{side_color}]{pos.get('direction', '')}[/]",
            f"${pos.get('amount', 0):.2f}",
            format_price(pos.get("entry_price", 0)),
            format_price(pos.get("current_price", 0)),
            format_pnl(pos.get("pnl", 0)),
        )

    table.add_section()
    table.add_row(
        "[bold]Total[/]", "", f"[bold]${exposure:.2f}[/]", "", "",
        format_pnl(pnl),
    )
    return table


def get_log_handler() -> RichHandler:
    return RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
