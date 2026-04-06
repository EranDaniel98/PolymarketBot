# Signal Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Batch all signal plugin evaluations per market before triggering decisions, so the engine aggregates with the full picture instead of making premature single-source decisions.

**Architecture:** The poller currently evaluates all plugins in parallel and publishes each signal individually via `"signal"` event. Each signal triggers `on_signal()` which queries the DB for recent signals — but sibling signals from the same cycle haven't been saved yet, so the engine only sees 1 source. The fix: poller collects all signals from one cycle, groups by market, and publishes a new `"signal_batch"` event. The engine handles the batch by saving all signals first, then making one decision per market. Individual `"signal"` events from fast_trader and thin_market_detector continue working as before (single-source downgrade is correct for those reactive paths). Shared decision logic is extracted into a private `_make_decision()` method to avoid code duplication between `on_signal` and `on_signal_batch`.

**Tech Stack:** Python 3.12+, asyncio, existing EventBus pub/sub

**Important behavior change:** After this change, the poller's main evaluation loop will NO LONGER publish individual `"signal"` events. Only `"signal_batch"` events will be published from the poller path. The `_evaluate_loop_once()` method is kept for backward compatibility (tests use it directly), but it is no longer called by `_evaluate_loop()`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `polymarket_bot/models.py` | Modify | Add `SignalBatchEvent` dataclass |
| `polymarket_bot/poller.py` | Modify | Collect signals per cycle, group by market, publish `signal_batch` |
| `polymarket_bot/decision/engine.py` | Modify | Extract `_make_decision()`, add `on_signal_batch()`, refactor `on_signal()` to use shared method |
| `polymarket_bot/app.py` | Modify | Subscribe to `signal_batch` event |
| `tests/test_poller.py` | Modify | Add batch publishing tests, negative test for no individual signals |
| `tests/test_decision/test_engine.py` | Modify | Add `on_signal_batch` tests |

---

### Task 1: Add `SignalBatchEvent` model

**Files:**
- Modify: `polymarket_bot/models.py:56-59`

- [ ] **Step 1: Add `SignalBatchEvent` dataclass after `SignalEvent`**

```python
# In models.py, after SignalEvent (line 59), add:

@dataclass(frozen=True)
class SignalBatchEvent:
    """All signals from one evaluation cycle for a single market."""
    signals: tuple[Signal, ...]
    market: Market
```

Note: Use `tuple` not `list` because the dataclass is `frozen=True` and lists are mutable.

- [ ] **Step 2: Commit**

```bash
git add polymarket_bot/models.py
git commit -m "feat: add SignalBatchEvent model for batched signal delivery"
```

---

### Task 2: Modify poller to batch signals per market

**Files:**
- Modify: `polymarket_bot/poller.py:72-118`
- Test: `tests/test_poller.py`

**Context:** The poller's `_evaluate_loop()` currently calls `_evaluate_loop_once()` for each (market, plugin) pair, and each call immediately publishes a `"signal"` event. We need to:
1. Keep `_evaluate_loop_once()` unchanged for backward compatibility (existing tests use it directly, and it's still the right abstraction for single plugin-market evaluation)
2. Add `_evaluate_cycle()` that collects signals, groups by market, and publishes `"signal_batch"` events
3. Change `_evaluate_loop()` to call `_evaluate_cycle()` instead of calling `_evaluate_loop_once()` per pair

**After this change:** `_evaluate_loop()` no longer publishes individual `"signal"` events. Only `"signal_batch"` events are published from the poller's main loop.

- [ ] **Step 1: Write failing tests for batch publishing**

Add to `tests/test_poller.py`:

```python
from polymarket_bot.models import SignalBatchEvent


async def test_poller_batches_signals_per_market(market):
    """All signals for a market from one cycle should be published as a single batch."""
    scanner = AsyncMock(spec=MarketScanner)

    signal_a = Signal(
        source="news", market_id=market.id, direction=Direction.YES,
        confidence=0.8, reasoning="News signal",
        timestamp=datetime.now(timezone.utc),
    )
    signal_b = Signal(
        source="llm", market_id=market.id, direction=Direction.YES,
        confidence=0.7, reasoning="LLM signal",
        timestamp=datetime.now(timezone.utc),
    )

    plugin_a = FakePlugin("news", signal_a)
    plugin_b = FakePlugin("llm", signal_b)

    bus = EventBus()
    batches = []
    bus.subscribe("signal_batch", lambda e: batches.append(e))

    poller = SignalPoller(
        scanner=scanner, plugins=[plugin_a, plugin_b], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 1
    assert len(batches[0].signals) == 2
    sources = {s.source for s in batches[0].signals}
    assert sources == {"news", "llm"}


async def test_poller_batch_groups_by_market():
    """Signals for different markets should produce separate batch events."""
    market_a = Market(
        id="m1", question="Q1?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xa", "NO": "0xb"}, current_price=0.50,
    )
    market_b = Market(
        id="m2", question="Q2?",
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tokens={"YES": "0xc", "NO": "0xd"}, current_price=0.60,
    )

    scanner = AsyncMock(spec=MarketScanner)

    signal_1 = Signal(
        source="news", market_id="m1", direction=Direction.YES,
        confidence=0.8, reasoning="", timestamp=datetime.now(timezone.utc),
    )
    signal_2 = Signal(
        source="news", market_id="m2", direction=Direction.NO,
        confidence=0.7, reasoning="", timestamp=datetime.now(timezone.utc),
    )

    class MarketAwarePlugin:
        def __init__(self, name, signals_by_market):
            self._name = name
            self._signals = signals_by_market
        @property
        def name(self): return self._name
        def can_evaluate(self, market): return market.id in self._signals
        async def evaluate(self, market): return self._signals.get(market.id)

    plugin = MarketAwarePlugin("news", {"m1": signal_1, "m2": signal_2})

    bus = EventBus()
    batches = []
    bus.subscribe("signal_batch", lambda e: batches.append(e))

    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market_a, market_b]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 2
    batch_market_ids = {b.market.id for b in batches}
    assert batch_market_ids == {"m1", "m2"}


async def test_poller_batch_skips_markets_with_no_signals(market):
    """Markets where no plugin produced a signal should NOT produce a batch event."""
    scanner = AsyncMock(spec=MarketScanner)

    plugin = FakePlugin("test", None)  # Returns None

    bus = EventBus()
    batches = []
    bus.subscribe("signal_batch", lambda e: batches.append(e))

    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(batches) == 0


async def test_poller_batch_does_not_publish_individual_signals(market):
    """_evaluate_cycle should NOT publish individual 'signal' events — only batches."""
    scanner = AsyncMock(spec=MarketScanner)

    signal_a = Signal(
        source="news", market_id=market.id, direction=Direction.YES,
        confidence=0.8, reasoning="",
        timestamp=datetime.now(timezone.utc),
    )
    plugin = FakePlugin("news", signal_a)

    bus = EventBus()
    individual_signals = []
    batches = []
    bus.subscribe("signal", lambda e: individual_signals.append(e))
    bus.subscribe("signal_batch", lambda e: batches.append(e))

    poller = SignalPoller(
        scanner=scanner, plugins=[plugin], event_bus=bus,
        scan_interval=9999, signal_interval=9999,
    )
    poller._markets = [market]
    poller._running = True

    await poller._evaluate_cycle()

    assert len(individual_signals) == 0
    assert len(batches) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_poller.py -v -k "batch"`
Expected: FAIL — `_evaluate_cycle` does not exist yet

- [ ] **Step 3: Implement `_evaluate_cycle()` and update `_evaluate_loop()`**

In `polymarket_bot/poller.py`, add `_evaluate_cycle()` method and replace `_evaluate_loop()` body:

```python
async def _evaluate_cycle(self) -> None:
    """Run all plugins against all markets, batch results per market, publish."""
    from polymarket_bot.models import SignalBatchEvent

    semaphore = asyncio.Semaphore(10)
    results: list[tuple[Market, Signal]] = []

    async def _eval(market: "Market", plugin: "SignalPlugin"):
        async with semaphore:
            try:
                signal = await plugin.evaluate(market)
                if signal and signal.confidence >= 0.1:
                    print_signal(
                        signal.source, market.id,
                        signal.direction.value, signal.confidence,
                    )
                    results.append((market, signal))
            except Exception:
                logger.debug(
                    "Plugin %s failed on market %s",
                    plugin.name, market.id[:16],
                )

    tasks = [
        _eval(m, p)
        for m in self._markets
        for p in self._plugins
        if p.can_evaluate(m)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Group signals by market and publish one batch per market
    by_market: dict[str, tuple[Market, list]] = {}
    for market, signal in results:
        if market.id not in by_market:
            by_market[market.id] = (market, [])
        by_market[market.id][1].append(signal)

    for _market_id, (market, signals) in by_market.items():
        event = SignalBatchEvent(signals=tuple(signals), market=market)
        await self._bus.publish("signal_batch", event)

async def _evaluate_loop(self) -> None:
    """Periodically run all signal plugins against all markets (batched per market)."""
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
```

Keep `_evaluate_loop_once()` unchanged — it's still used by existing tests for the individual signal path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_poller.py -v`
Expected: All tests pass (old tests for `_evaluate_loop_once` still pass, new batch tests pass)

- [ ] **Step 5: Commit**

```bash
git add polymarket_bot/poller.py tests/test_poller.py
git commit -m "feat: poller batches signals per market before publishing"
```

---

### Task 3: Extract `_make_decision()` and add `on_signal_batch()`

**Files:**
- Modify: `polymarket_bot/decision/engine.py`
- Test: `tests/test_decision/test_engine.py`

**Context:** The decision logic from `on_signal()` lines 273-386 (aggregation → action → risk check → publish) will be extracted into a shared `_make_decision(market, recent_signals)` method. Both `on_signal()` and the new `on_signal_batch()` will call it after their respective signal-gathering logic. This eliminates ~130 lines of duplication and prevents future divergences.

`on_signal_batch()` differs from `on_signal()` only in how signals are gathered:
- `on_signal()`: saves 1 signal to DB, queries DB for recent signals, deduplicates
- `on_signal_batch()`: saves N signals to DB, queries DB for prior-cycle signals (filtering out batch sources to avoid duplicates), merges with batch signals, deduplicates

- [ ] **Step 1: Write failing tests for `on_signal_batch`**

Add to `tests/test_decision/test_engine.py`:

```python
from polymarket_bot.models import SignalBatchEvent


@pytest.mark.asyncio
async def test_on_signal_batch_multi_source_auto_executes(engine, market, mock_db):
    """A batch with 2+ sources at high confidence should auto_execute (not downgrade)."""
    signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.90, reasoning="LLM says yes",
               timestamp=datetime.now(timezone.utc)),
        Signal(source="bookmaker", market_id="m1", direction=Direction.YES,
               confidence=0.85, reasoning="Odds favor yes",
               timestamp=datetime.now(timezone.utc)),
    )
    event = SignalBatchEvent(signals=signals, market=market)
    await engine.on_signal_batch(event)

    calls = engine._bus.publish.call_args_list
    topics = [c[0][0] for c in calls]
    assert "trade_decision" in topics


@pytest.mark.asyncio
async def test_on_signal_batch_saves_all_signals(engine, market, mock_db):
    """All signals in a batch should be saved to DB."""
    signals = (
        Signal(source="news", market_id="m1", direction=Direction.YES,
               confidence=0.6, reasoning="", timestamp=datetime.now(timezone.utc)),
        Signal(source="whale", market_id="m1", direction=Direction.YES,
               confidence=0.7, reasoning="", timestamp=datetime.now(timezone.utc)),
    )
    event = SignalBatchEvent(signals=signals, market=market)
    await engine.on_signal_batch(event)

    assert mock_db.save_signal.call_count == 2
    assert mock_db.save_signal_outcome.call_count == 2


@pytest.mark.asyncio
async def test_on_signal_batch_single_source_still_downgrades(engine, market, mock_db):
    """A batch with only 1 signal should still be downgraded by min_signal_sources gate."""
    signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.90, reasoning="", timestamp=datetime.now(timezone.utc)),
    )
    event = SignalBatchEvent(signals=signals, market=market)
    await engine.on_signal_batch(event)

    # Should have saved signal even if decision was downgraded
    assert mock_db.save_signal.call_count == 1
    # Should NOT have published trade_decision (single source → downgraded)
    calls = engine._bus.publish.call_args_list
    topics = [c[0][0] for c in calls]
    assert "trade_decision" not in topics


@pytest.mark.asyncio
async def test_on_signal_batch_merges_with_prior_db_signals(engine, market, mock_db):
    """Batch should merge with signals from prior cycles stored in DB."""
    # DB has a prior-cycle divergence signal
    mock_db.get_signals.return_value = [{
        "source": "divergence", "market_id": "m1",
        "direction": "YES", "confidence": 0.75, "reasoning": "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]

    # Batch has an llm signal
    signals = (
        Signal(source="llm", market_id="m1", direction=Direction.YES,
               confidence=0.85, reasoning="",
               timestamp=datetime.now(timezone.utc)),
    )
    event = SignalBatchEvent(signals=signals, market=market)
    await engine.on_signal_batch(event)

    # Should see 2 distinct sources (llm from batch + divergence from DB)
    calls = engine._bus.publish.call_args_list
    topics = [c[0][0] for c in calls]
    # With 2 sources, auto_approve should promote to auto_execute
    assert "trade_decision" in topics
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision/test_engine.py -v -k "batch"`
Expected: FAIL — `on_signal_batch` does not exist

- [ ] **Step 3: Extract `_make_decision()` from `on_signal()`**

In `polymarket_bot/decision/engine.py`, add the import at the top:

```python
from polymarket_bot.models import (
    ArbitrageOpportunity, Direction, Market, OrderType, Signal,
    SignalBatchEvent, SignalEvent, TradeDecision,
)
```

Extract the shared decision logic (currently `on_signal` lines 273-386) into a new private method. Place it before `on_signal()`:

```python
async def _make_decision(self, market: Market, recent_signals: list[Signal]) -> None:
    """Shared decision logic: aggregate signals, check risk, publish or reject."""
    import logging as _logging
    _slog = _logging.getLogger("polymarket_bot.structured")

    composite = self.aggregate_signals(recent_signals)
    action = self.determine_action(composite)

    if action == "notify" and self._thresholds.auto_approve_all:
        logger.info("Auto-approve mode: promoting notify->auto_execute for %s", market.id)
        action = "auto_execute"

    distinct_sources = {s.source for s in recent_signals}
    if action == "auto_execute" and len(distinct_sources) < self._thresholds.min_signal_sources:
        logger.warning(
            "Downgraded auto_execute->notify for %s: only %d source(s) (%s)",
            market.id, len(distinct_sources), ", ".join(distinct_sources),
        )
        action = "notify"

    if action == "log_only":
        _slog.info(
            "Skipped: %.2f for %s", composite, market.id[:16],
            extra={
                "event_type": "trade_skipped",
                "market_id": market.id,
                "question": market.question,
                "category": market.category or "",
                "confidence": composite,
                "market_price": market.current_price,
                "volume": market.volume,
                "signals": [
                    {"source": s.source, "direction": s.direction.value,
                     "confidence": round(s.confidence, 3)}
                    for s in recent_signals
                ],
                "distinct_sources": len(distinct_sources),
            },
        )
        return

    direction = self.determine_majority_direction(recent_signals)
    size = await self._risk.calculate_position_size(composite, market.current_price)

    _slog.info(
        "Decision: %s %s $%.2f (%s)", direction.value, market.id[:16], size, action,
        extra={
            "event_type": "trade_decision",
            "market_id": market.id,
            "question": market.question,
            "category": market.category or "",
            "direction": direction.value,
            "amount": size,
            "confidence": composite,
            "action": action,
            "market_price": market.current_price,
            "volume": market.volume,
            "signals": [
                {"source": s.source, "direction": s.direction.value,
                 "confidence": round(s.confidence, 3),
                 "reasoning": s.reasoning[:500]}
                for s in recent_signals
            ],
            "distinct_sources": len(distinct_sources),
        },
    )

    decision = TradeDecision(
        market_id=market.id,
        direction=direction,
        amount=size,
        confidence=composite,
        signals=recent_signals,
        order_type=OrderType.LIMIT,
        tokens=market.tokens,
        question=market.question,
        category=market.category or _infer_category(market.question),
    )

    approved, reason = await self._risk.check(decision, market.current_price)
    if not approved:
        rotated = False
        if "Max exposure" in reason:
            rotated = await self._try_rotation(decision, market.current_price, _slog)

        if not rotated:
            _slog.warning(
                "Risk rejected: %s %s — %s", direction.value, market.id[:16], reason,
                extra={
                    "event_type": "risk_rejected",
                    "market_id": market.id,
                    "question": market.question,
                    "direction": direction.value,
                    "amount": size,
                    "confidence": composite,
                    "market_price": market.current_price,
                    "rejection_reason": reason,
                    "signals": [s.source for s in recent_signals],
                },
            )
        return

    trade_id = f"{market.id}_{direction.value}_{datetime.now(timezone.utc).isoformat()}"
    await self._db.save_trade_signals(trade_id, recent_signals)

    if action == "auto_execute":
        await self._bus.publish("trade_decision", decision)
    elif action == "notify":
        await self._bus.publish("approval_request", decision)
```

- [ ] **Step 4: Refactor `on_signal()` to use `_make_decision()`**

Replace the decision logic in `on_signal()` (lines 273-386) with a single call:

```python
async def on_signal(self, signal_event: SignalEvent) -> None:
    if self._risk.circuit_breaker_active:
        logger.warning("Circuit breaker active — ignoring signal")
        return

    # Skip markets where we already hold a position
    if hasattr(self, '_exit_manager') and self._exit_manager:
        if signal_event.market.id in self._exit_manager._positions:
            logger.debug("Already holding position in %s — skipping", signal_event.market.id)
            return

    # Short-circuit when exposure is maxed and no rotation possible
    exposure_pct = await self._exposure_ratio()
    if exposure_pct >= self._risk._config.rotation_exposure_threshold:
        if not (hasattr(self, '_exit_manager') and self._exit_manager
                and self._exit_manager._positions):
            logger.debug("Exposure at %.0f%% with no positions to rotate — skipping %s",
                        exposure_pct * 100, signal_event.market.id)
            return

    signal = signal_event.signal
    market = signal_event.market
    await self._db.save_signal(signal)
    await self._db.save_signal_outcome(
        source=signal.source,
        market_id=signal.market_id,
        predicted_direction=signal.direction.value,
        confidence=signal.confidence,
        market_price=market.current_price,
        timestamp=signal.timestamp,
    )

    recent_rows = await self._db.get_signals(market.id)
    recent_signals = [signal]
    for row in recent_rows:
        try:
            recent_signals.append(Signal(
                source=row["source"],
                market_id=row["market_id"],
                direction=Direction(row["direction"]),
                confidence=row["confidence"],
                reasoning=row.get("reasoning", ""),
                timestamp=datetime.fromisoformat(row["timestamp"]),
            ))
        except (KeyError, ValueError):
            continue
    # Deduplicate by source — keep most recent per source
    seen_sources: dict[str, Signal] = {}
    for s in recent_signals:
        if s.source not in seen_sources or s.timestamp > seen_sources[s.source].timestamp:
            seen_sources[s.source] = s
    recent_signals = list(seen_sources.values())

    await self._make_decision(market, recent_signals)
```

- [ ] **Step 5: Add `on_signal_batch()` method**

Add after `on_signal()`:

```python
async def on_signal_batch(self, batch: SignalBatchEvent) -> None:
    """Handle a batch of signals from one evaluation cycle for a single market.

    Saves all signals first, then makes one decision with the full picture.
    """
    if self._risk.circuit_breaker_active:
        logger.warning("Circuit breaker active — ignoring signal batch")
        return

    market = batch.market

    # Skip markets where we already hold a position
    if hasattr(self, '_exit_manager') and self._exit_manager:
        if market.id in self._exit_manager._positions:
            logger.debug("Already holding position in %s — skipping batch", market.id)
            return

    # Short-circuit when exposure is maxed and no rotation possible
    exposure_pct = await self._exposure_ratio()
    if exposure_pct >= self._risk._config.rotation_exposure_threshold:
        if not (hasattr(self, '_exit_manager') and self._exit_manager
                and self._exit_manager._positions):
            logger.debug("Exposure at %.0f%% with no positions to rotate — skipping %s",
                        exposure_pct * 100, market.id)
            return

    # Save ALL signals to DB first
    for signal in batch.signals:
        await self._db.save_signal(signal)
        await self._db.save_signal_outcome(
            source=signal.source,
            market_id=signal.market_id,
            predicted_direction=signal.direction.value,
            confidence=signal.confidence,
            market_price=market.current_price,
            timestamp=signal.timestamp,
        )

    # Build signal list: batch signals + prior-cycle DB signals
    # Filter out sources already in the batch to avoid duplicates
    batch_sources = {s.source for s in batch.signals}
    recent_rows = await self._db.get_signals(market.id)
    all_signals: list[Signal] = list(batch.signals)
    for row in recent_rows:
        if row["source"] in batch_sources:
            continue  # already have fresher version from batch
        try:
            all_signals.append(Signal(
                source=row["source"],
                market_id=row["market_id"],
                direction=Direction(row["direction"]),
                confidence=row["confidence"],
                reasoning=row.get("reasoning", ""),
                timestamp=datetime.fromisoformat(row["timestamp"]),
            ))
        except (KeyError, ValueError):
            continue

    # Deduplicate by source — keep most recent per source
    seen_sources: dict[str, Signal] = {}
    for s in all_signals:
        if s.source not in seen_sources or s.timestamp > seen_sources[s.source].timestamp:
            seen_sources[s.source] = s
    recent_signals = list(seen_sources.values())

    await self._make_decision(market, recent_signals)
```

- [ ] **Step 6: Run all tests to verify refactor didn't break anything**

Run: `python -m pytest tests/test_decision/test_engine.py -v`
Expected: ALL tests pass — old `on_signal` tests still pass (refactored to use `_make_decision` internally), new batch tests pass

- [ ] **Step 7: Commit**

```bash
git add polymarket_bot/decision/engine.py tests/test_decision/test_engine.py
git commit -m "feat: extract _make_decision(), add on_signal_batch for batched decisions"
```

---

### Task 4: Wire `signal_batch` event in app.py

**Files:**
- Modify: `polymarket_bot/app.py:245-247`

- [ ] **Step 1: Add `signal_batch` subscription**

In `polymarket_bot/app.py`, at the event wiring section (line 245-247), add the new subscription:

```python
    # Wire event handlers
    bus.subscribe("signal", decision_engine.on_signal)
    bus.subscribe("signal_batch", decision_engine.on_signal_batch)
    bus.subscribe("arb_opportunity", decision_engine.on_arb_opportunity)
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add polymarket_bot/app.py
git commit -m "feat: subscribe to signal_batch events for batched decision-making"
```

---

### Task 5: Verify end-to-end and deploy

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Verify signal flow logic**

Confirm these paths work correctly:
- **Poller path (batched):** poller → `signal_batch` → `on_signal_batch()` → decision with all sources
- **Fast trader path (individual):** fast_trader → `signal` → `on_signal()` → decision (single-source downgrade is correct)
- **Thin market path (individual):** thin_market_detector → `signal` → `on_signal()` → decision (single-source downgrade is correct)

- [ ] **Step 3: Commit all changes together if not already committed**

```bash
git add -A
git commit -m "fix: batch signal evaluation — engine sees all sources before deciding

Previously each signal plugin published independently, causing premature
single-source decisions. Now the poller collects all signals per market
from one evaluation cycle and publishes them as a batch. The engine
saves all signals to DB first, then aggregates with the full picture.

Shared decision logic extracted into _make_decision() to eliminate
duplication between on_signal() and on_signal_batch().

Fast trader and thin market detector still use individual signal path
where single-source downgrade is intentionally correct."
```

- [ ] **Step 4: Deploy to Railway**

```bash
railway up
```

- [ ] **Step 5: Check logs for multi-source decisions**

```bash
railway logs -n 50
```

Expected: Decisions should now show 2+ distinct sources in the structured logs instead of being downgraded to notify. Look for:
- `"distinct_sources": 2` or higher in decision logs
- Fewer `"Downgraded auto_execute->notify"` warnings from the main evaluation cycle
- Fast trader / thin market signals still correctly show single-source behavior

---

## Summary of Changes

| Before | After |
|--------|-------|
| Each plugin publishes `"signal"` immediately | Poller collects all signals, groups by market, publishes `"signal_batch"` |
| `on_signal()` sees 1 source, downgrades to notify | `on_signal_batch()` sees all sources, can auto_execute |
| Decision logic duplicated if adding new handler | Shared `_make_decision()` used by both paths |
| Fast trader / thin market: individual signals | Unchanged — still use `"signal"` → `on_signal()` |
| No `SignalBatchEvent` model | New `SignalBatchEvent(signals, market)` dataclass |
