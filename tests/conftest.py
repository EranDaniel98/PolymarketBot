"""Pytest fixtures for the polymarket_weather test suite."""

import os

# Set dashboard auth secret BEFORE any test imports the dashboard module.
# polymarket_weather.api.dashboard calls install_auth() at import time which
# fails fast without DASH_PASS. Tests must not run against real prod secrets.
os.environ.setdefault("DASH_PASS", "test_dashboard_password_long_enough_1234")
