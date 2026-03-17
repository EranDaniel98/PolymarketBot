import asyncio
import logging
import sys
from pathlib import Path

from polymarket_bot import __version__
from polymarket_bot.cli import (
    console, get_log_handler, print_banner, print_trade_execution,
    print_signal, print_arb_opportunity, print_circuit_breaker,
)
from polymarket_bot.config import load_config, BotConfig
from polymarket_bot.database import Database
from polymarket_bot.decision.engine import DecisionEngine
from polymarket_bot.decision.risk import RiskManager
from polymarket_bot.event_bus import EventBus
from polymarket_bot.execution.engine import ExecutionEngine
from polymarket_bot.arbitrage.detector import OpportunityDetector
from polymarket_bot.arbitrage.mapper import MarketMapper
from polymarket_bot.arbitrage.monitor import PriceMonitor
from polymarket_bot.models import (
    SignalEvent, TradeDecision, TradeExecution, ArbitrageOpportunity,
)
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.signals.base import SignalPlugin
from polymarket_bot.signals.news import NewsSignal
from polymarket_bot.signals.social import SocialSignal
from polymarket_bot.signals.polls import PollSignal
from polymarket_bot.signals.llm import LLMSignal
from polymarket_bot.signals.bookmaker import BookmakerSignal

logger = logging.getLogger("polymarket_bot")


def setup_logging():
    handler = get_log_handler()
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(message)s",
        datefmt="[%X]",
    )


def build_signal_plugins(config: BotConfig) -> list[SignalPlugin]:
    plugins = []
    sc = config.signals
    if sc.news.enabled:
        plugins.append(NewsSignal(api_key=sc.news.newsapi_key, poll_interval=sc.news.poll_interval))
    if sc.social.enabled:
        plugins.append(SocialSignal(
            subreddits=sc.social.subreddits,
            poll_interval=sc.social.poll_interval,
        ))
    if sc.polls.enabled:
        plugins.append(PollSignal(poll_interval=sc.polls.poll_interval))
    if sc.llm.enabled:
        plugins.append(LLMSignal(api_key=sc.llm.anthropic_api_key, model=sc.llm.model))
    if sc.bookmaker.enabled:
        plugins.append(BookmakerSignal(
            api_key=sc.bookmaker.odds_api_key, poll_interval=sc.bookmaker.poll_interval,
        ))
    return plugins


async def run_bot(config_path: str = "config.yaml"):
    setup_logging()
    print_banner(__version__)

    # Load config
    config = load_config(Path(config_path))
    console.print(f"[bold green]Config loaded[/] from {config_path}")

    # Initialize core
    db = Database(Path("polymarket_bot.db"))
    await db.initialize()
    console.print("[bold green]Database initialized[/]")

    bus = EventBus()

    # Execution engine (initialized first to fetch bankroll)
    exec_engine = ExecutionEngine(config=config.execution, database=db, event_bus=bus)
    await exec_engine.start(
        api_key=config.polymarket.api_key,
        api_secret=config.polymarket.api_secret,
        private_key=config.polymarket.private_key,
        chain_id=config.polymarket.chain_id,
    )
    console.print("[bold green]Execution engine ready[/]")

    # Risk manager — fetch real bankroll from wallet
    bankroll = await exec_engine.get_balance()
    if bankroll is None or bankroll <= 0:
        console.print("[bold yellow]WARNING: Could not fetch wallet balance, using default $5000[/]")
        bankroll = 5000.0
    console.print(f"[bold green]Wallet balance:[/] ${bankroll:.2f}")
    risk_manager = RiskManager(config=config.risk, database=db, bankroll=bankroll)

    # Decision engine
    decision_engine = DecisionEngine(
        risk_manager=risk_manager, event_bus=bus, database=db,
        thresholds=config.confidence_thresholds, signals_config=config.signals,
    )

    # Notifications
    notifiers = []
    if config.notifications.telegram.enabled:
        from polymarket_bot.notifications.telegram import TelegramNotifier
        tg = TelegramNotifier(
            bot_token=config.notifications.telegram.bot_token,
            chat_id=config.notifications.telegram.chat_id,
            approval_timeout=config.notifications.telegram.approval_timeout,
        )
        await tg.start()
        notifiers.append(tg)
        console.print("[bold green]Telegram notifier active[/]")

    if config.notifications.discord.enabled:
        from polymarket_bot.notifications.discord import DiscordNotifier
        dc = DiscordNotifier(webhook_url=config.notifications.discord.webhook_url)
        await dc.start()
        notifiers.append(dc)
        console.print("[bold green]Discord notifier active[/]")

    # Arbitrage engine
    mapper = MarketMapper()
    detector = OpportunityDetector(min_spread=config.arbitrage.min_spread)
    monitor = PriceMonitor(
        mapper=mapper, detector=detector, event_bus=bus, database=db,
        poll_interval=config.arbitrage.poll_interval,
    )

    # Wire event handlers
    bus.subscribe("signal", decision_engine.on_signal)
    bus.subscribe("arb_opportunity", decision_engine.on_arb_opportunity)

    async def on_trade_decision(decision: TradeDecision):
        current_price = monitor.get_cached_price("polymarket", decision.market_id)
        if current_price is None or current_price <= 0:
            logger.warning("No live price for %s — skipping execution", decision.market_id)
            return
        print_trade_execution(decision.market_id, decision.direction.value,
                             decision.amount, current_price)
        await exec_engine.execute(decision, current_price=current_price)

    async def on_approval_request(decision: TradeDecision):
        for notifier in notifiers:
            approved = await notifier.request_approval(decision)
            if approved:
                fresh_price = monitor.get_cached_price("polymarket", decision.market_id)
                if fresh_price is None or fresh_price <= 0:
                    logger.warning("No live price for %s after approval — skipping", decision.market_id)
                    return
                await exec_engine.execute(decision, current_price=fresh_price)
                return
        logger.info("Trade not approved: %s", decision.market_id)

    async def on_trade_execution(execution: TradeExecution):
        print_trade_execution(execution.market_id, execution.direction.value,
                             execution.amount, execution.price)
        new_balance = await exec_engine.get_balance()
        if new_balance and new_balance > 0:
            risk_manager.update_bankroll(new_balance)
        for notifier in notifiers:
            await notifier.send_trade_notification(
                execution.market_id, execution.direction.value,
                execution.amount, execution.price,
            )

    bus.subscribe("trade_decision", on_trade_decision)
    bus.subscribe("approval_request", on_approval_request)
    bus.subscribe("trade_execution", on_trade_execution)

    # Signal plugins
    plugins = build_signal_plugins(config)
    for plugin in plugins:
        await plugin.start()
        console.print(f"[bold green]Signal plugin started:[/] [cyan]{plugin.name}[/]")

    # Start arbitrage monitor
    await monitor.start()
    console.print("[bold green]Arbitrage monitor started[/]")

    console.print("\n[bold cyan]Bot is running. Press Ctrl+C to stop.[/]\n")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        console.print("\n[bold yellow]Shutting down...[/]")
        await monitor.stop()
        for plugin in plugins:
            await plugin.stop()
        await exec_engine.stop()
        for notifier in notifiers:
            await notifier.stop()
        await db.close()
        console.print("[bold green]Shutdown complete.[/]")
