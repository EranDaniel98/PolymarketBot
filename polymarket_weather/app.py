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
    from polymarket_weather.db import persistence
    init_db(config.database.url)
    session_factory = get_session_factory()
    # Auto-create tables on first run (Alembic not wired yet — Phase 7.4)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Phase 2.1: add missing columns to trades table on existing deploys
    await persistence.ensure_schema(session_factory)
    logger.info("Database initialized")

    # Event bus
    bus = EventBus()

    # City mapper
    from polymarket_weather.weather.city_mapper import CityMapper
    city_mapper = CityMapper(Path(config.cities.file))
    logger.info("City mapper loaded: %d cities", len(city_mapper.all_city_names()))

    # Seed IcaoStation rows for all configured stations so the metar_readings
    # FK has somewhere to point. Idempotent upsert — safe on every boot.
    from polymarket_weather.db.models import IcaoStation
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy import select as sa_select
    async with session_factory() as session:
        for city in city_mapper.all_cities():
            primary = city.get("primary_station")
            if not primary:
                continue
            # Use SELECT-then-INSERT pattern (portable across PG + SQLite
            # in tests; pg_insert upsert would be PG-only).
            exists = await session.execute(
                sa_select(IcaoStation.station_id).where(
                    IcaoStation.station_id == primary
                )
            )
            if exists.scalar_one_or_none() is not None:
                continue
            session.add(IcaoStation(
                station_id=primary,
                city_name=city["city_aliases"][0],
                country_code=city.get("country", "XX")[:2],
                lat=city["lat"],
                lon=city["lon"],
                is_active=True,
            ))
        await session.commit()
    logger.info("IcaoStation seed complete")

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
    from polymarket_weather.trading.positions import PositionManager, TrackedPosition
    positions = PositionManager(
        edge_inversion_threshold=config.trading.edge_inversion_threshold,
    )

    # Phase 2.1: reconcile open positions from DB on startup so state survives
    # process restarts. Any in-memory dict from a prior run is gone, but the
    # DB has every status='open' trade with full metadata.
    open_rows = await persistence.load_open_positions(session_factory)
    for r in open_rows:
        positions._positions[r.market_id] = TrackedPosition(
            market_id=r.market_id,
            direction=r.direction,
            entry_price=r.entry_price,
            size_usdc=r.size_usdc,
            city=r.city,
            event_id=r.event_id,
            entry_time=r.entry_time,
            peak_pnl_pct=r.peak_pnl_pct,
        )
        # Mirror into the RiskManager's exposure dict too
        risk.record_entry(r.market_id, r.city, r.region, r.size_usdc)
    if open_rows:
        logger.info("Restored %d open positions from DB", len(open_rows))

    # Phase 2.2: pull daily_loss and completed_trades counters from DB state
    dl = await persistence.get_daily_loss(session_factory)
    ct = await persistence.get_completed_trades(session_factory)
    if dl > 0:
        risk._daily_loss = dl
    risk._completed_trades = ct
    logger.info("Restored state: daily_loss=%.2f completed_trades=%d", dl, ct)

    # Phase 2.4: shared lock serializing the mismatch → risk-check → execute
    # → record critical section so concurrent scheduler ticks can't race.
    trade_lock = asyncio.Lock()

    # Wire shared state into the dashboard so /api/overview, /api/metrics etc.
    # can read real data. Was missed in the original build-out.
    from polymarket_weather.api.dashboard import set_state
    set_state(
        session_factory=session_factory,
        positions=positions,
        risk=risk,
        executor=executor,
        config=config,
        scanner=scanner,
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

    # Phase 4: instantiate the mismatch pipeline once. It holds references
    # to all the collaborators it needs and is stateless per-call.
    from polymarket_weather.trading.pipeline import MismatchPipeline
    pipeline = MismatchPipeline(
        city_mapper=city_mapper,
        forecast_engine=forecast,
        metar_collector=metar,
        nwp_fetcher=nwp,
        risk_manager=risk,
        executor=executor,
        position_manager=positions,
        session_factory=session_factory,
        trade_lock=trade_lock,
        notifier=notifier,
        edge_config=config.edge,
        fee_config=config.fee,
        trading_config=config.trading,
        risk_config=config.risk,
    )

    async def market_scan_and_mismatch_job():
        markets = await scanner.fetch_weather_markets()
        logger.info("Market scan: %d weather markets", len(markets))
        if not markets:
            return
        traded = 0
        skipped: dict[str, int] = {}
        for m in markets:
            result = await pipeline.evaluate(m)
            if result.decision == "traded":
                traded += 1
            else:
                skipped[result.reason] = skipped.get(result.reason, 0) + 1
        if traded or skipped:
            logger.info("Pipeline: %d traded, skips=%s", traded, dict(skipped))

    async def stale_data_check_job():
        stale = await metar.check_staleness(station_ids, config.weather.metar.stale_threshold)
        if stale:
            logger.warning("Stale stations: %s", stale)
            if notifier:
                for sid in stale:
                    await notifier.send_stale_station(sid, config.weather.metar.stale_threshold / 3600)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Structured-concurrency scheduler: each job runs in its own task inside
    # a TaskGroup. Failure in one job is logged but doesn't cancel the others.
    # Fix 1.1 — replaces the old no-op `while True: await asyncio.sleep(60)`.
    from polymarket_weather.runtime import interval_runner

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                interval_runner("metar_poll", metar_poll_job, config.scheduler.metar_poll),
                name="metar_poll",
            )
            tg.create_task(
                interval_runner(
                    "market_scan_and_mismatch",
                    market_scan_and_mismatch_job,
                    config.scheduler.market_scan,
                ),
                name="market_scan_and_mismatch",
            )
            tg.create_task(
                interval_runner(
                    "stale_data_check",
                    stale_data_check_job,
                    config.scheduler.stale_data_check,
                ),
                name="stale_data_check",
            )
    except* asyncio.CancelledError:
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
