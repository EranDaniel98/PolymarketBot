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

Phase 8.1: a global JobRegistry tracks last-run timestamps and last error
per job so the dashboard can surface scheduler health. Read via
`get_job_registry()` or `polymarket_weather.runtime.JOB_REGISTRY`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

JobFunc = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# Phase 8.1: Job health registry
# ---------------------------------------------------------------------------

@dataclass
class JobStatus:
    name: str
    interval_seconds: float
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_duration_ms: float | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    successes: int = 0
    failures: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_finished_at": self.last_finished_at.isoformat() if self.last_finished_at else None,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "successes": self.successes,
            "failures": self.failures,
            "healthy": self.is_healthy(),
        }

    def is_healthy(self) -> bool:
        """A job is healthy if it has run at least once recently and the last
        run did not error. 'Recently' = within 2x its configured interval.
        """
        if self.last_finished_at is None:
            return False
        age = (datetime.now(timezone.utc) - self.last_finished_at).total_seconds()
        if age > self.interval_seconds * 2:
            return False
        return self.last_error is None


@dataclass
class JobRegistry:
    jobs: dict[str, JobStatus] = field(default_factory=dict)

    def register(self, name: str, interval_seconds: float) -> JobStatus:
        status = self.jobs.get(name)
        if status is None:
            status = JobStatus(name=name, interval_seconds=interval_seconds)
            self.jobs[name] = status
        return status

    def all(self) -> list[JobStatus]:
        return list(self.jobs.values())


JOB_REGISTRY = JobRegistry()


def get_job_registry() -> JobRegistry:
    return JOB_REGISTRY


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
    Records per-cycle health into JOB_REGISTRY for the /api/jobs endpoint.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    status = JOB_REGISTRY.register(name, interval_seconds)

    async def _run_cycle() -> None:
        status.last_started_at = datetime.now(timezone.utc)
        t0 = time.monotonic()
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scheduled job '%s' raised", name)
            status.last_error = f"{type(exc).__name__}: {exc}"
            status.last_error_at = datetime.now(timezone.utc)
            status.failures += 1
        else:
            status.last_error = None
            status.successes += 1
        finally:
            status.last_finished_at = datetime.now(timezone.utc)
            status.last_duration_ms = (time.monotonic() - t0) * 1000.0

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
