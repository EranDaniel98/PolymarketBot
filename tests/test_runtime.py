"""Tests for the interval_runner scheduler helper (Phase 1.1)."""

import asyncio

import pytest

from polymarket_weather.runtime import interval_runner


@pytest.mark.asyncio
async def test_interval_runner_fires_immediately_when_run_once_at_start():
    calls: list[int] = []

    async def job():
        calls.append(1)

    task = asyncio.create_task(
        interval_runner("test", job, interval_seconds=10, run_once_at_start=True)
    )
    await asyncio.sleep(0.05)  # let the first cycle happen
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_interval_runner_repeats_on_interval():
    calls: list[int] = []

    async def job():
        calls.append(1)

    task = asyncio.create_task(
        interval_runner("test", job, interval_seconds=0.05, run_once_at_start=True)
    )
    await asyncio.sleep(0.22)  # ~4 cycles (0, 0.05, 0.10, 0.15, 0.20)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(calls) >= 3, f"expected >= 3 cycles, got {len(calls)}"


@pytest.mark.asyncio
async def test_interval_runner_swallows_job_exceptions():
    calls: list[int] = []

    async def bad_job():
        calls.append(1)
        raise RuntimeError("boom")

    task = asyncio.create_task(
        interval_runner("test", bad_job, interval_seconds=0.02, run_once_at_start=True)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Despite raising, the runner must keep cycling
    assert len(calls) >= 3


@pytest.mark.asyncio
async def test_interval_runner_propagates_cancellation():
    async def job():
        pass

    task = asyncio.create_task(
        interval_runner("test", job, interval_seconds=10)
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_interval_runner_rejects_zero_interval():
    import asyncio as _asyncio

    async def job():
        pass

    async def _run():
        with pytest.raises(ValueError):
            await interval_runner("test", job, interval_seconds=0)

    _asyncio.run(_run())


@pytest.mark.asyncio
async def test_interval_runner_cancels_during_job_execution():
    """If cancel arrives while the job is running, it should propagate cleanly."""
    started = asyncio.Event()
    finished = False

    async def slow_job():
        nonlocal finished
        started.set()
        await asyncio.sleep(10)
        finished = True

    task = asyncio.create_task(
        interval_runner("test", slow_job, interval_seconds=1)
    )
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not finished
