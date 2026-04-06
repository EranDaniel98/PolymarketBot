"""Main application entrypoint — wires all components together and runs the bot."""

import asyncio
import logging
from pathlib import Path

from polymarket_weather import __version__
from polymarket_weather.config import load_config
from polymarket_weather.event_bus import EventBus

logger = logging.getLogger("polymarket_weather")


async def run_bot(config_path: str = "config.yaml"):
    """Main entry point. Initializes all components and runs the trading loop."""
    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("Polymarket Weather Arbitrage Bot v%s starting...", __version__)

    # Load config
    config = load_config(Path(config_path))
    logger.info("Config loaded from %s", config_path)

    if config.trading.paper_trading:
        logger.info(">>> PAPER TRADING MODE — no real orders <<<")

    # Initialize database
    from polymarket_weather.db.session import init_db, get_session_factory, get_engine, dispose_db
    from polymarket_weather.db.models import Base
    init_db(config.database.url)
    session_factory = get_session_factory()
    # Auto-create tables on first run (Alembic not wired yet)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized")

    # Event bus
    bus = EventBus()

    # City mapper
    from polymarket_weather.weather.city_mapper import CityMapper
    city_mapper = CityMapper(Path(config.cities.file))
    logger.info("City mapper loaded: %d cities", len(city_mapper.all_city_names()))

    # Weather collector
    from polymarket_weather.weather.collector import MetarCollector
    metar = MetarCollector(
        api_url=config.weather.metar.api_url,
        user_agent=config.weather.metar.user_agent,
        hours_lookback=config.weather.metar.hours_lookback,
        max_results=config.weather.metar.max_results_per_request,
        session_factory=session_factory,
    )
    await metar.start()

    # NWP fetcher
    from polymarket_weather.weather.nwp import NwpFetcher
    nwp = NwpFetcher(
        api_url=config.weather.nwp.api_url,
        models=config.weather.nwp.models,
        deterministic_url=config.weather.nwp.deterministic_url,
        deterministic_models=config.weather.nwp.deterministic_models,
    )
    await nwp.start()

    # Market scanner
    from polymarket_weather.markets.scanner import WeatherMarketScanner
    scanner = WeatherMarketScanner(
        gamma_api_url=config.markets.gamma_api_url,
        discovery_endpoint=config.markets.discovery_endpoint,
        weather_tag_discovery=config.markets.weather_tag_discovery,
        fallback_keywords=config.markets.fallback_keywords,
    )
    await scanner.start()

    # Forecast engine
    from polymarket_weather.weather.forecast import ForecastEngine
    forecast = ForecastEngine(
        metar_only_hours=config.forecast.metar_only_hours,
        blend_cutoff_hours=config.forecast.blend_cutoff_hours,
        metar_blend_weight=config.forecast.metar_blend_weight,
        distribution_df=config.forecast.distribution_df,
        min_confidence=config.forecast.min_confidence,
        rmse_by_horizon=config.forecast.rmse_by_horizon,
    )

    # Risk manager
    from polymarket_weather.trading.risk import RiskManager
    risk = RiskManager(
        max_position_usdc=config.risk.max_position_usdc,
        max_total_exposure_usdc=config.risk.max_total_exposure_usdc,
        max_open_positions=config.risk.max_open_positions,
        daily_loss_cap_usdc=config.risk.daily_loss_cap_usdc,
        max_exposure_per_city_usdc=config.risk.max_exposure_per_city_usdc,
        max_exposure_per_region_usdc=config.risk.max_exposure_per_region_usdc,
        drawdown_pause_pct=config.risk.drawdown_pause_pct,
        bootstrap_trades=config.risk.bootstrap_trades,
        bootstrap_size_usdc=config.risk.bootstrap_size_usdc,
        min_trade_size_usdc=config.risk.min_trade_size_usdc,
    )

    # Trade executor
    from polymarket_weather.trading.executor import TradeExecutor
    executor = TradeExecutor(
        paper_trading=config.trading.paper_trading,
        paper_balance=config.trading.paper_balance,
        max_slippage=config.trading.slippage_tolerance,
        max_retries=config.trading.max_retries,
    )
    await executor.start(
        api_key=config.polymarket.api_key,
        api_secret=config.polymarket.api_secret,
        private_key=config.polymarket.private_key,
        chain_id=config.polymarket.chain_id,
    )

    # Position manager
    from polymarket_weather.trading.positions import PositionManager
    positions = PositionManager(
        edge_inversion_threshold=config.trading.edge_inversion_threshold,
    )

    # Telegram alerts
    notifier = None
    if config.notifications.telegram.enabled:
        from polymarket_weather.alerts.telegram import WeatherTelegramNotifier
        notifier = WeatherTelegramNotifier(
            bot_token=config.notifications.telegram.bot_token,
            chat_id=config.notifications.telegram.chat_id,
            alert_on=config.notifications.telegram.alert_on,
        )
        await notifier.start()
        logger.info("Telegram notifier active")

    # Build scheduler jobs
    from polymarket_weather.scheduler import build_schedules
    schedules = build_schedules(
        metar_poll=config.scheduler.metar_poll,
        taf_poll=config.scheduler.taf_poll,
        nwp_poll=config.scheduler.nwp_poll,
        market_scan=config.scheduler.market_scan,
        mismatch_detection=config.scheduler.mismatch_detection,
        trade_execution=config.scheduler.trade_execution,
        position_monitor=config.scheduler.position_monitor,
        settlement_check=config.scheduler.settlement_check,
        stale_data_check=config.scheduler.stale_data_check,
        daily_report=config.scheduler.daily_report,
        calibration_update=config.scheduler.calibration_update,
    )
    logger.info("Configured %d scheduled jobs", len(schedules))

    # --- Job functions ---
    station_ids = city_mapper.all_station_ids()

    async def metar_poll_job():
        count = await metar.fetch_and_store(station_ids)
        logger.info("METAR poll: %d new readings", count)

    async def market_scan_and_mismatch_job():
        markets = await scanner.fetch_weather_markets()
        logger.info("Market scan: %d weather markets", len(markets))
        # TODO: run mismatch detection on scanned markets

    async def stale_data_check_job():
        stale = await metar.check_staleness(station_ids, config.weather.metar.stale_threshold)
        if stale:
            logger.warning("Stale stations: %s", stale)
            if notifier:
                for sid in stale:
                    await notifier.send_stale_station(sid, config.weather.metar.stale_threshold / 3600)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Simple polling loop (APScheduler integration in future iteration)
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")
        await metar.stop()
        await nwp.stop()
        await scanner.stop()
        await executor.stop()
        if notifier:
            await notifier.stop()
        await dispose_db()
        logger.info("Shutdown complete.")
