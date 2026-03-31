import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from polymarket_bot import __version__
from polymarket_bot.cli import (
    console, get_log_handler, print_banner, print_trade_execution,
    print_signal, print_arb_opportunity, print_circuit_breaker,
    build_full_dashboard, format_pnl,
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
    Direction, Market, OrderStatus, SignalEvent, TradeDecision, TradeExecution,
    ArbitrageOpportunity,
)
from polymarket_bot.calibrator import WeightCalibrator
from polymarket_bot.exit_manager import ExitManager, ExitRule
from polymarket_bot.fast_trader import FastTrader
from polymarket_bot.market_filter import MarketFilter
from polymarket_bot.resolution_tracker import ResolutionTracker
from polymarket_bot.notifications.base import NotificationLevel
from polymarket_bot.poller import SignalPoller
from polymarket_bot.scanner import MarketScanner
from polymarket_bot.signals.base import SignalPlugin
from polymarket_bot.signals.news import NewsSignal
from polymarket_bot.signals.social import SocialSignal
from polymarket_bot.signals.polls import PollSignal
from polymarket_bot.signals.llm import LLMSignal
from polymarket_bot.signals.bookmaker import BookmakerSignal
from polymarket_bot.signals.favorite_longshot import FavoriteLongshotSignal
from polymarket_bot.signals.divergence import DivergenceSignal
from polymarket_bot.signals.weather import WeatherSignal
from polymarket_bot.signals.whale import WhaleSignal
from polymarket_bot.signals.crypto_price import CryptoPriceSignal
from polymarket_bot.arbitrage.structural_arb import StructuralArbDetector
from polymarket_bot.thin_market_detector import ThinMarketDetector

logger = logging.getLogger("polymarket_bot")


def setup_logging():
    handler = get_log_handler()
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(message)s",
        datefmt="[%X]",
    )
    # Suppress noisy HTTP request logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


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
        plugins.append(LLMSignal(
            api_key=sc.llm.anthropic_api_key,
            model=sc.llm.model,
            screening_model=sc.llm.screening_model,
            newsapi_key=sc.news.newsapi_key if sc.news.enabled else "",
            openai_api_key=sc.llm.openai_api_key,
            ensemble_enabled=sc.llm.ensemble_enabled,
            ensemble_models=sc.llm.ensemble_models,
            aggregation=sc.llm.aggregation,
            confidence_discount=sc.llm.confidence_discount,
        ))
    if sc.bookmaker.enabled:
        plugins.append(BookmakerSignal(
            api_key=sc.bookmaker.odds_api_key, poll_interval=sc.bookmaker.poll_interval,
        ))
    if sc.favorite_longshot.enabled:
        fl = sc.favorite_longshot
        plugins.append(FavoriteLongshotSignal(
            min_price_short=fl.min_price_short,
            max_price_long=fl.max_price_long,
            min_volume=fl.min_volume,
            min_days=fl.min_days,
        ))
    if sc.divergence.enabled:
        plugins.append(DivergenceSignal(
            min_divergence=sc.divergence.min_divergence,
            min_forecasters=sc.divergence.min_forecasters,
            min_days=sc.divergence.min_days,
        ))
    if sc.weather.enabled:
        plugins.append(WeatherSignal())
    if sc.whale.enabled:
        plugins.append(WhaleSignal(
            single_trade_threshold=sc.whale.single_trade_threshold,
            cumulative_threshold=sc.whale.cumulative_threshold,
            window_seconds=sc.whale.window_seconds,
            tracked_wallets=sc.whale.tracked_wallets,
            poll_interval=sc.whale.poll_interval,
        ))
    if sc.crypto_price.enabled:
        plugins.append(CryptoPriceSignal(
            exchanges=sc.crypto_price.exchanges,
            min_divergence=sc.crypto_price.min_divergence,
            max_days_to_expiry=sc.crypto_price.max_days_to_expiry,
            poll_interval=sc.crypto_price.poll_interval,
        ))
    return plugins


async def run_bot(config_path: str = "config.yaml"):
    setup_logging()
    print_banner(__version__)

    # Load config
    config = load_config(Path(config_path))
    console.print(f"[bold green]Config loaded[/] from {config_path}")

    # File logging (structured JSON) — resolve relative to config dir
    data_dir = Path(config_path).parent
    if config.logging.file_enabled:
        from polymarket_bot.logging_config import setup_file_logging
        log_path = data_dir / config.logging.file_path
        file_handler = setup_file_logging(
            log_path,
            max_bytes=config.logging.max_size_mb * 1024 * 1024,
            backup_count=config.logging.backup_count,
        )
        logging.getLogger().addHandler(file_handler)
        console.print(f"[bold green]File logging:[/] {log_path}")

    # Paper trading mode banner
    if config.execution.paper_trading:
        console.print(
            "[bold yellow]>>> PAPER TRADING MODE — no real orders will be placed <<<[/]\n"
        )

    # Initialize core — DB and logs live next to config file
    data_dir = Path(config_path).parent
    db = Database(data_dir / "polymarket_bot.db")
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
        bankroll = config.execution.paper_balance
        console.print(f"[bold yellow]Using paper balance: ${bankroll:.2f}[/]")
    console.print(f"[bold green]Wallet balance:[/] ${bankroll:.2f}")
    risk_manager = RiskManager(
        config=config.risk, database=db, bankroll=bankroll, fee_config=config.fee,
    )

    # Weight calibrator — auto-adjusts signal weights based on track record
    calibrator = WeightCalibrator(database=db, min_samples=20, recalibrate_every=10)

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
            auto_approve_window=config.notifications.telegram.auto_approve_window,
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

    # Market cache — shared lookup for token resolution
    market_cache: dict[str, Market] = {}

    # Exit manager — monitors positions and triggers sells
    exit_rules = ExitRule(
        take_profit=config.exit.take_profit,
        stop_loss=config.exit.stop_loss,
        trailing_stop=config.exit.trailing_stop,
        edge_gone_threshold=config.exit.edge_gone_threshold,
        time_decay_hours=config.exit.time_decay_hours,
        trailing_stop_activation=config.exit.trailing_stop_activation,
        max_hold_hours=config.exit.max_hold_hours,
    )
    exit_mgr = ExitManager(event_bus=bus, database=db, rules=exit_rules, check_interval=30)
    risk_manager._exit_manager = exit_mgr
    decision_engine.set_exit_manager(exit_mgr)
    decision_engine.set_market_cache(market_cache)
    exit_mgr.set_price_getter(monitor.get_cached_price)
    # Cooldown is now armed in on_trade_execution after confirmed fill

    # Load persisted positions from DB
    await exit_mgr.load_from_db()
    if exit_mgr._positions:
        console.print(
            f"[bold green]Loaded {len(exit_mgr._positions)} open positions from DB[/]"
        )

    # Recover positions from trades that weren't tracked (crash recovery)
    untracked = await db.get_untracked_trades()
    for row in untracked:
        await exit_mgr.track_entry(
            row["market_id"], Direction(row["direction"]),
            row["price"], row["amount"],
        )
    if untracked:
        console.print(f"[bold yellow]Recovered {len(untracked)} untracked positions from crash[/]")

    # Wire event handlers
    bus.subscribe("signal", decision_engine.on_signal)
    bus.subscribe("signal_batch", decision_engine.on_signal_batch)
    bus.subscribe("arb_opportunity", decision_engine.on_arb_opportunity)

    async def on_trade_decision(decision: TradeDecision):
        current_price = monitor.get_cached_price("polymarket", decision.market_id)
        if current_price is None or current_price <= 0:
            logger.warning("No live price for %s — skipping execution", decision.market_id)
            return
        print_trade_execution(decision.market_id, decision.direction.value,
                             decision.amount, current_price,
                             question=decision.question)
        await exec_engine.execute(decision, current_price=current_price)

    async def on_approval_request(decision: TradeDecision):
        for notifier in notifiers:
            approved = await notifier.request_approval(decision)
            if approved:
                fresh_price = monitor.get_cached_price("polymarket", decision.market_id)
                if fresh_price is None or fresh_price <= 0:
                    logger.warning("No live price for %s after approval — skipping",
                                  decision.market_id)
                    return
                await exec_engine.execute(decision, current_price=fresh_price)
                return
        logger.info("Trade not approved: %s", decision.market_id)

    async def on_trade_execution(execution: TradeExecution):
        nonlocal bankroll

        # Only process confirmed fills — ignore PLACED/PENDING/FAILED events
        if execution.status not in (OrderStatus.FILLED, OrderStatus.PARTIAL_FILL):
            logger.info("Ignoring non-fill execution: %s status=%s",
                       execution.market_id[:16], execution.status.value)
            return

        cached_market = market_cache.get(execution.market_id)
        exec_question = cached_market.question if cached_market else ""
        print_trade_execution(execution.market_id, execution.direction.value,
                             execution.amount, execution.price,
                             question=exec_question)
        if execution.is_exit:
            # Confirmed exit fill — now safe to remove position and arm cooldown
            await exit_mgr.track_exit(execution.market_id)
            risk_manager.record_exit(execution.market_id)
            # If this was a rotation exit, publish the queued entry now
            await decision_engine.on_rotation_exit_filled(execution.market_id)
        else:
            # Track new entries for exit management — only on confirmed fill
            tokens = cached_market.tokens if cached_market else {}
            end_date = cached_market.end_date if cached_market else None
            category = cached_market.category if cached_market else ""
            await exit_mgr.track_entry(
                execution.market_id, execution.direction,
                execution.price, execution.amount,
                tokens=tokens,
                end_date=end_date,
                category=category or "",
            )
        new_balance = await exec_engine.get_balance()
        if new_balance and new_balance > 0:
            bankroll = new_balance
            risk_manager.update_bankroll(new_balance)
        # Recalibrate signal weights based on performance
        recalibrated = await calibrator.maybe_recalibrate()
        if recalibrated:
            decision_engine._weights = calibrator.weights
            console.print("[cyan]Signal weights recalibrated:[/] " +
                         ", ".join(f"{k}={v:.0%}" for k, v in calibrator.weights.items()))
        cached = market_cache.get(execution.market_id)
        question = cached.question if cached else ""
        for notifier in notifiers:
            await notifier.send_trade_notification(
                execution.market_id, execution.direction.value,
                execution.amount, execution.price,
                question=question,
            )

    bus.subscribe("trade_decision", on_trade_decision)
    bus.subscribe("approval_request", on_approval_request)
    bus.subscribe("trade_execution", on_trade_execution)

    # Signal plugins
    plugins = build_signal_plugins(config)
    for plugin in plugins:
        await plugin.start()
        console.print(f"[bold green]Signal plugin started:[/] [cyan]{plugin.name}[/]")

    # Wire price monitor to CryptoPriceSignal for WebSocket-cached prices
    crypto_plugin = next((p for p in plugins if p.name == "crypto_price"), None)
    if crypto_plugin:
        crypto_plugin.set_price_monitor(monitor)

    # Wire "Decide for me" callback for Telegram
    llm_plugin = next((p for p in plugins if p.name == "llm"), None)
    if llm_plugin:
        async def auto_decide(decision: TradeDecision) -> bool:
            market = market_cache.get(decision.market_id)
            if not market:
                return False
            signal = await llm_plugin.evaluate(market)
            if signal is None:
                return False
            return signal.direction == decision.direction and signal.confidence >= 0.4

        for notifier in notifiers:
            if hasattr(notifier, "auto_decide_callback"):
                notifier.auto_decide_callback = auto_decide

    # Start arbitrage monitor and exit manager
    await monitor.start()
    console.print("[bold green]Arbitrage monitor started[/]")
    await exit_mgr.start()
    console.print("[bold green]Exit manager started[/]")

    # Market scanner + smart filtering + signal polling loop
    scanner = MarketScanner(max_markets=200)
    await scanner.start()
    market_filter = MarketFilter()
    poller = SignalPoller(
        scanner=scanner,
        plugins=plugins,
        event_bus=bus,
        market_filter=market_filter,
        scan_interval=300,    # refresh market list every 5 min
        signal_interval=120,  # evaluate signals every 2 min
    )
    await poller.start()

    # Populate market cache and subscribe to price feeds
    # Build token_id -> condition_id mapping for the price monitor
    token_to_condition: dict[str, str] = {}
    for m in poller._markets:
        market_cache[m.id] = m
        if m.tokens:
            yes_token = m.tokens.get("YES", "")
            if yes_token:
                token_to_condition[yes_token] = m.id
    # Subscribe using YES token IDs (CLOB uses token IDs, not condition IDs)
    monitor.subscribe_markets(list(token_to_condition.keys()), token_to_condition)

    # Structural arbitrage detector
    structural_arb_task = None
    if config.arbitrage.structural_arb.enabled:
        arb_cfg = config.arbitrage.structural_arb
        struct_detector = StructuralArbDetector(
            fee_rate=arb_cfg.fee_rate, min_profit_pct=arb_cfg.min_profit_pct,
        )

        async def _structural_arb_loop():
            while True:
                try:
                    for m in list(poller._markets):
                        opp = struct_detector.check(m)
                        if opp:
                            amount = min(arb_cfg.max_position_usd, bankroll * config.risk.max_position_pct)
                            await exec_engine.execute_structural_arb(
                                opp, amount_per_side=amount,
                                cancel_timeout=arb_cfg.cancel_timeout,
                            )
                except Exception:
                    logger.exception("Structural arb loop error")
                await asyncio.sleep(30)

        structural_arb_task = asyncio.create_task(_structural_arb_loop())
        console.print("[bold green]Structural arbitrage detector started[/]")
    else:
        console.print("[dim]Structural arbitrage disabled[/]")

    # Fast trader — monitors breaking news every 20s for rapid trades
    fast_trader = None
    if config.signals.fast_trader.enabled:
        fast_trader = FastTrader(
            event_bus=bus,
            markets=poller._markets,
            poll_interval=20,
            newsapi_key=config.signals.news.newsapi_key if config.signals.news.enabled else "",
        )
        await fast_trader.start()
    else:
        console.print("[dim]Fast trader disabled[/]")

    # Thin/new market detector — fast-tracks LLM analysis on low-volume markets
    thin_detector = ThinMarketDetector(
        event_bus=bus,
        llm_plugin=llm_plugin,
        poll_interval=600,
    )
    await thin_detector.start()
    console.print("[bold green]Thin market detector started[/]")

    # Resolution tracker — polls for market outcomes to measure signal accuracy
    resolution_tracker = ResolutionTracker(database=db, poll_interval=300)
    await resolution_tracker.start()
    console.print("[bold green]Resolution tracker started[/]")

    # Periodic circuit breaker reset check (every 30 minutes)
    async def _circuit_breaker_reset_loop():
        while True:
            await asyncio.sleep(1800)
            try:
                reset = await risk_manager.maybe_reset_circuit_breaker()
                if reset:
                    console.print("[bold green]Circuit breaker reset — trading resumed[/]")
            except Exception:
                logger.exception("Circuit breaker reset check failed")

    cb_reset_task = asyncio.create_task(_circuit_breaker_reset_loop())

    # Daily report scheduler
    async def _daily_report_loop():
        while True:
            now = datetime.now(timezone.utc)
            next_midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            await asyncio.sleep((next_midnight - now).total_seconds())
            try:
                stats = {
                    "daily_pnl": await db.get_daily_pnl(),
                    "total_pnl": await db.get_total_pnl(),
                    "trade_count": len(await db.get_daily_trades()),
                    "win_rate": await db.get_win_rate(),
                    "open_positions": len(exit_mgr._positions),
                    "bankroll": bankroll,
                }
                for n in notifiers:
                    if hasattr(n, "send_daily_report"):
                        await n.send_daily_report(stats)
            except Exception:
                logger.exception("Daily report failed")

    daily_task = asyncio.create_task(_daily_report_loop())

    # Web dashboard (optional)
    if config.web.enabled:
        try:
            import uvicorn
            from polymarket_bot.web.server import app as web_app
            web_app.state.db = db
            web_app.state.exit_mgr = exit_mgr
            web_app.state.market_cache = market_cache
            web_config = uvicorn.Config(
                web_app, host=config.web.host, port=config.web.port, log_level="warning",
            )
            web_server = uvicorn.Server(web_config)
            asyncio.create_task(web_server.serve())
            console.print(
                f"[bold green]Web dashboard:[/] http://{config.web.host}:{config.web.port}"
            )
        except ImportError:
            logger.warning("uvicorn/fastapi not installed — web dashboard disabled")

    console.print("\n[bold cyan]Bot is running. Press Ctrl+C to stop.[/]\n")

    # Live dashboard loop
    from rich.live import Live
    from polymarket_bot.cli import _time_ago
    start_time = time.time()

    with Live(console=console, refresh_per_second=0.2, transient=False) as live:
        try:
            while True:
                now = datetime.now(timezone.utc)

                # --- Positions ---
                positions_data = []
                correlated_exposure: dict[str, float] = {}
                for mid, pos in exit_mgr._positions.items():
                    cp = monitor.get_cached_price("polymarket", mid)
                    if cp and pos.direction == Direction.YES:
                        pnl_val = (cp - pos.entry_price) * pos.amount / pos.entry_price
                    elif cp and pos.direction == Direction.NO:
                        pnl_val = (pos.entry_price - cp) * pos.amount / pos.entry_price
                    else:
                        pnl_val = 0
                    cached = market_cache.get(mid)
                    label = cached.question if cached else mid
                    held = _time_ago(pos.entry_time) if pos.entry_time else ""
                    days_left = (pos.end_date - now).total_seconds() / 86400 if pos.end_date else 0
                    expires = f"{days_left:.0f}d" if pos.end_date and days_left > 0 else ""
                    positions_data.append({
                        "market_id": label, "direction": pos.direction.value,
                        "amount": pos.amount, "entry_price": pos.entry_price,
                        "current_price": cp or pos.entry_price, "pnl": pnl_val,
                        "peak_pnl_pct": pos.peak_pnl_pct,
                        "held": held, "expires": expires,
                    })
                    cat = pos.category or "other"
                    correlated_exposure[cat] = correlated_exposure.get(cat, 0) + pos.amount

                # --- Database queries ---
                daily_pnl = await db.get_daily_pnl()
                total_exp = await db.get_total_exposure()
                t_count = await db.get_trade_count()
                total_pnl = await db.get_total_pnl()
                win_rate = await db.get_win_rate()

                # --- Recent signals ---
                raw_signals = await db.get_recent_signals(limit=20)
                recent_signals = []
                for sig in raw_signals:
                    ts = sig.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts)
                        time_str = _time_ago(dt)
                    except (ValueError, TypeError):
                        time_str = ""
                    sig_market = market_cache.get(sig.get("market_id", ""))
                    market_label = sig_market.question[:35] if sig_market else sig.get("market_id", "")[:16]
                    recent_signals.append({
                        "time": time_str,
                        "source": sig.get("source", ""),
                        "direction": sig.get("direction", ""),
                        "confidence": sig.get("confidence", 0),
                        "market": market_label,
                    })

                # --- Recent trades ---
                raw_trades = await db.get_daily_trades()
                recent_trades = []
                for t in raw_trades[:10]:
                    ts = t.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts)
                        time_str = _time_ago(dt)
                    except (ValueError, TypeError):
                        time_str = ""
                    t_market = market_cache.get(t.get("market_id", ""))
                    market_label = t_market.question[:30] if t_market else t.get("market_id", "")[:16]
                    recent_trades.append({
                        "time": time_str,
                        "direction": t.get("direction", ""),
                        "amount": t.get("amount", 0),
                        "price": t.get("price", 0),
                        "pnl": t.get("realized_pnl", 0),
                        "market": market_label,
                    })

                # --- Plugin stats ---
                accuracy_report = await db.get_accuracy_report()
                plugin_stats = []
                for plugin in plugins:
                    acc_data = accuracy_report.get(plugin.name)
                    plugin_stats.append({
                        "name": plugin.name,
                        "active": True,
                        "signal_count": acc_data["n_signals"] if acc_data else 0,
                        "accuracy": acc_data["accuracy"] if acc_data else None,
                        "weight": decision_engine._weights.get(plugin.name, 0),
                    })

                max_daily_loss = bankroll * config.risk.max_daily_loss_pct

                dashboard = build_full_dashboard(
                    positions_data, daily_pnl, total_exp, bankroll,
                    t_count, time.time() - start_time,
                    config.execution.paper_trading,
                    total_pnl=total_pnl,
                    win_rate=win_rate,
                    circuit_breaker=risk_manager.circuit_breaker_active,
                    recovery=risk_manager.in_recovery,
                    recent_signals=recent_signals,
                    recent_trades=recent_trades,
                    plugin_stats=plugin_stats,
                    max_daily_loss=max_daily_loss,
                    correlated_exposure=correlated_exposure,
                )
                live.update(dashboard)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            console.print("\n[bold yellow]Shutting down...[/]")
            daily_task.cancel()
            cb_reset_task.cancel()
            if structural_arb_task:
                structural_arb_task.cancel()
            if fast_trader:
                await fast_trader.stop()
            await thin_detector.stop()
            await resolution_tracker.stop()
            await poller.stop()
            await exit_mgr.stop()
            await scanner.stop()
            await monitor.stop()
            for plugin in plugins:
                await plugin.stop()
            await exec_engine.stop()
            for notifier in notifiers:
                await notifier.stop()
            await db.close()
            console.print("[bold green]Shutdown complete.[/]")
