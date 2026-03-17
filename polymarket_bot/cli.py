import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.layout import Layout

console = Console()

COLOR_SCHEME = {
    "profit": "bold green",
    "loss": "bold red",
    "warning": "bold yellow",
    "signal": "bold cyan",
    "arb": "bold magenta",
    "info": "bold blue",
    "muted": "dim white",
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


def print_trade_execution(market_id: str, direction: str, amount: float, price: float) -> None:
    color = COLOR_SCHEME["profit"] if direction == "YES" else COLOR_SCHEME["loss"]
    console.print(
        Panel(
            f"[{color}]{direction}[/] {format_price(price)} x ${amount:.2f}\n"
            f"[{COLOR_SCHEME['muted']}]Market: {market_id}[/]",
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
