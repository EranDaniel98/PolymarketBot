import os

# Set dashboard auth secret BEFORE any test imports the dashboard module.
# polymarket_weather.api.dashboard calls install_auth() at import time which
# fails fast without DASH_PASS. Tests must not run against real prod secrets.
os.environ.setdefault("DASH_PASS", "test_dashboard_password_long_enough_1234")

import pytest  # noqa: E402
from pathlib import Path  # noqa: E402

# Legacy polymarket_bot fixtures — kept for backwards compat with old tests.
# Wrapped in try/except so new polymarket_weather-only test runs don't break
# if the legacy package is deleted in Phase 7.3 of the hardening plan.
try:
    from polymarket_bot.event_bus import EventBus
    from polymarket_bot.database import Database

    @pytest.fixture
    def event_bus():
        return EventBus()

    @pytest.fixture
    async def database(tmp_path):
        db = Database(tmp_path / "test.db")
        await db.initialize()
        yield db
        await db.close()
except ImportError:
    pass
