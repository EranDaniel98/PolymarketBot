"""Lightweight interval-runner for scheduled async jobs.

Used instead of APScheduler 4.x (alpha-only) or 3.x (extra heavyweight).
Each job runs in its own task inside an `asyncio.TaskGroup`; failures in
one job are logged but don't cancel the others.

Design notes:
  - `run_once_at_start=True` executes the job immediately, then waits.
  - Jitter (up to 10% of interval) is added to the first sleep so jobs
    don't all fire in sync on a cold start.
  - `asyncio.CancelledError` is always re-raised so shutdown propagates.
  - Per-cycle exceptions are logged with exc_info and swallowed so a bad
    METAR fetch doesn't kill the whole scheduler.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

JobFunc = Callable[[], Awaitable[None]]


async def interval_runner(
    name: str,
    job: JobFunc,
    interval_seconds: float,
    *,
    run_once_at_start: bool = True,
    jitter_fraction: float = 0.10,
) -> None:
    """Run `job` every `interval_seconds` until cancelled.

    Intended to be wrapped in `asyncio.TaskGroup.create_task` from app.py.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    async def _run_cycle() -> None:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled job '%s' raised", name)

    # Optional initial jitter so parallel jobs don't all fire at t=0.
    initial_delay = random.uniform(0, interval_seconds * jitter_fraction)
    if run_once_at_start:
        logger.info("scheduler: starting job '%s' (every %.0fs)", name, interval_seconds)
        await _run_cycle()
    else:
        await asyncio.sleep(initial_delay)

    while True:
        await asyncio.sleep(interval_seconds)
        await _run_cycle()
