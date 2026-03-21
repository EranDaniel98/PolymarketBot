"""Signal polling loop — periodically evaluates all signal plugins against active markets."""

import asyncio
import logging

from polymarket_bot.cli import console, print_signal
from polymarket_bot.event_bus import EventBus
from polymarket_bot.market_filter import MarketFilter
from polymarket_bot.models import Market, SignalBatchEvent, SignalEvent
from polymarket_bot.scanner import MarketScanner
from polymarket_bot.signals.base import SignalPlugin

logger = logging.getLogger(__name__)


class SignalPoller:
    def __init__(
        self,
        scanner: MarketScanner,
        plugins: list[SignalPlugin],
        event_bus: EventBus,
        market_filter: MarketFilter | None = None,
        scan_interval: int = 300,
        signal_interval: int = 120,
    ):
        self._scanner = scanner
        self._plugins = plugins
        self._bus = event_bus
        self._filter = market_filter or MarketFilter()
        self._scan_interval = scan_interval
        self._signal_interval = signal_interval
        self._markets: list[Market] = []
        self._running = False
        self._scan_task: asyncio.Task | None = None
        self._eval_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        # Do initial scan immediately, then filter
        raw_markets = await self._scanner.fetch_active_markets()
        self._markets = self._filter.filter_and_rank(raw_markets)
        console.print(f"[bold green]Tracking {len(self._markets)} active markets[/]")
        # Start background loops
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._eval_task = asyncio.create_task(self._evaluate_loop())

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
        if self._eval_task:
            self._eval_task.cancel()

    async def _scan_loop(self) -> None:
        """Periodically refresh the list of active markets."""
        while self._running:
            await asyncio.sleep(self._scan_interval)
            try:
                raw = await self._scanner.fetch_active_markets()
                new_markets = self._filter.filter_and_rank(raw) if raw else []
                if new_markets:
                    old_count = len(self._markets)
                    self._markets = new_markets
                    if len(self._markets) != old_count:
                        console.print(
                            f"[cyan]Markets refreshed:[/] {len(self._markets)} active "
                            f"(was {old_count})"
                        )
            except Exception:
                logger.exception("Market scan failed")

    async def _evaluate_loop_once(self, market: "Market", plugin: "SignalPlugin") -> None:
        """Evaluate a single plugin against a single market. Used by the loop and tests."""
        if not plugin.can_evaluate(market):
            return
        try:
            signal = await plugin.evaluate(market)
            if signal and signal.confidence >= 0.1:
                print_signal(
                    signal.source, market.id,
                    signal.direction.value, signal.confidence,
                )
                event = SignalEvent(signal=signal, market=market)
                await self._bus.publish("signal", event)
        except Exception:
            logger.debug(
                "Plugin %s failed on market %s",
                plugin.name, market.id[:16],
            )

    async def _evaluate_cycle(self) -> None:
        """Collect all signals from one cycle, group by market, publish signal_batch events."""
        semaphore = asyncio.Semaphore(10)
        # signals_by_market: market_id -> (market, [signals])
        signals_by_market: dict[str, tuple[Market, list]] = {}

        async def _eval_one(market: Market, plugin: "SignalPlugin") -> None:
            if not plugin.can_evaluate(market):
                return
            try:
                signal = await plugin.evaluate(market)
                if signal and signal.confidence >= 0.1:
                    if market.id not in signals_by_market:
                        signals_by_market[market.id] = (market, [])
                    signals_by_market[market.id][1].append(signal)
            except Exception:
                logger.debug(
                    "Plugin %s failed on market %s",
                    plugin.name, market.id[:16],
                )

        async def _eval_with_sem(market, plugin):
            async with semaphore:
                await _eval_one(market, plugin)

        tasks = [
            _eval_with_sem(m, p)
            for m in self._markets
            for p in self._plugins
            if p.can_evaluate(m)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        for market, signals in signals_by_market.values():
            event = SignalBatchEvent(signals=tuple(signals), market=market)
            await self._bus.publish("signal_batch", event)

    async def _evaluate_loop(self) -> None:
        """Periodically run all signal plugins against all markets (parallel with semaphore)."""
        while self._running:
            if not self._markets or not self._plugins:
                await asyncio.sleep(self._signal_interval)
                continue

            logger.info(
                "Evaluating %d plugins x %d markets",
                len(self._plugins), len(self._markets),
            )

            await self._evaluate_cycle()

            logger.info("Signal evaluation cycle complete")
            await asyncio.sleep(self._signal_interval)
