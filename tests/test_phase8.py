"""Phase 8 — observability + kill switch tests."""

import asyncio
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from polymarket_weather.api.dashboard import app, set_state
from polymarket_weather.db.models import Base
from polymarket_weather.runtime import (
    JOB_REGISTRY,
    JobRegistry,
    JobStatus,
    interval_runner,
)


# ---------------------------------------------------------------------------
# 8.1 Job health registry
# ---------------------------------------------------------------------------

class TestJobRegistry:
    def setup_method(self):
        # Reset the global registry between tests so they don't bleed.
        JOB_REGISTRY.jobs.clear()

    def test_register_creates_status(self):
        reg = JobRegistry()
        s = reg.register("foo", interval_seconds=60)
        assert s.name == "foo"
        assert s.interval_seconds == 60
        assert s.last_finished_at is None
        assert s.is_healthy() is False  # Never run

    def test_register_is_idempotent(self):
        reg = JobRegistry()
        s1 = reg.register("foo", interval_seconds=60)
        s2 = reg.register("foo", interval_seconds=120)  # Different interval ignored
        assert s1 is s2
        assert s1.interval_seconds == 60  # Original wins

    def test_status_to_dict_serializable(self):
        s = JobStatus(name="foo", interval_seconds=60)
        s.last_started_at = datetime.now(timezone.utc)
        s.last_finished_at = datetime.now(timezone.utc)
        s.successes = 5
        d = s.to_dict()
        assert d["name"] == "foo"
        assert d["successes"] == 5
        assert "last_started_at" in d

    def test_healthy_after_recent_run(self):
        s = JobStatus(name="foo", interval_seconds=60)
        s.last_finished_at = datetime.now(timezone.utc)
        s.last_error = None
        assert s.is_healthy() is True

    def test_unhealthy_after_error(self):
        s = JobStatus(name="foo", interval_seconds=60)
        s.last_finished_at = datetime.now(timezone.utc)
        s.last_error = "boom"
        assert s.is_healthy() is False

    def test_unhealthy_when_overdue(self):
        from datetime import timedelta
        s = JobStatus(name="foo", interval_seconds=60)
        s.last_finished_at = datetime.now(timezone.utc) - timedelta(seconds=300)
        assert s.is_healthy() is False

    @pytest.mark.asyncio
    async def test_interval_runner_records_success(self):
        JOB_REGISTRY.jobs.clear()
        calls = []

        async def job():
            calls.append(1)

        task = asyncio.create_task(
            interval_runner("test_job", job, interval_seconds=0.05, run_once_at_start=True)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        status = JOB_REGISTRY.jobs.get("test_job")
        assert status is not None
        assert status.successes >= 1
        assert status.failures == 0
        assert status.last_error is None
        assert status.last_finished_at is not None

    @pytest.mark.asyncio
    async def test_interval_runner_records_failure(self):
        JOB_REGISTRY.jobs.clear()

        async def bad_job():
            raise RuntimeError("kaboom")

        task = asyncio.create_task(
            interval_runner("bad_job", bad_job, interval_seconds=0.05, run_once_at_start=True)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        status = JOB_REGISTRY.jobs.get("bad_job")
        assert status is not None
        assert status.failures >= 1
        assert "kaboom" in (status.last_error or "")
        assert status.last_error_at is not None


# ---------------------------------------------------------------------------
# 8.1 /api/jobs endpoint
# ---------------------------------------------------------------------------

class TestJobsEndpoint:
    def setup_method(self):
        JOB_REGISTRY.jobs.clear()

    def test_jobs_endpoint_requires_auth(self):
        client = TestClient(app)
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_jobs_endpoint_returns_list(self):
        # Pre-populate one job
        JOB_REGISTRY.register("test_metar", interval_seconds=1800)
        client = TestClient(app)
        resp = client.get(
            "/api/jobs",
            headers={"X-API-Key": os.environ["DASH_PASS"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = {j["name"] for j in data}
        assert "test_metar" in names
        # Schema check
        sample = next(j for j in data if j["name"] == "test_metar")
        for key in ["name", "interval_seconds", "last_started_at",
                    "last_finished_at", "last_error", "successes",
                    "failures", "healthy"]:
            assert key in sample


# ---------------------------------------------------------------------------
# 8.4 Kill switch
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_post_requires_auth(self, db_session_factory):
        set_state(session_factory=db_session_factory)
        client = TestClient(app)
        resp = client.post("/api/kill_switch", json={"paused": True})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_kill_switch_get_requires_auth(self):
        client = TestClient(app)
        resp = client.get("/api/kill_switch")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_kill_switch_pause_then_read(self, db_session_factory):
        from unittest.mock import MagicMock
        risk = MagicMock()
        risk.pause = MagicMock()
        risk.resume = MagicMock()
        set_state(session_factory=db_session_factory, risk=risk)

        client = TestClient(app)
        headers = {"X-API-Key": os.environ["DASH_PASS"]}

        # Pause
        resp = client.post("/api/kill_switch", json={"paused": True}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["paused"] is True
        risk.pause.assert_called_once()

        # Read back
        resp = client.get("/api/kill_switch", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["paused"] is True

        # Resume
        resp = client.post("/api/kill_switch", json={"paused": False}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["paused"] is False
        risk.resume.assert_called_once()

        resp = client.get("/api/kill_switch", headers=headers)
        assert resp.json()["paused"] is False

    @pytest.mark.asyncio
    async def test_kill_switch_503_when_no_db(self):
        # Reset state to remove session_factory
        set_state(session_factory=None)
        client = TestClient(app)
        headers = {"X-API-Key": os.environ["DASH_PASS"]}
        resp = client.post("/api/kill_switch", json={"paused": True}, headers=headers)
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_kill_switch_get_returns_default_when_no_db(self):
        set_state(session_factory=None)
        client = TestClient(app)
        headers = {"X-API-Key": os.environ["DASH_PASS"]}
        resp = client.get("/api/kill_switch", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] is False
        assert data["available"] is False
