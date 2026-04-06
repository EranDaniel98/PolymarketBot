"""Tests for bearer-token auth + rate limiting + config validation."""

import os

import pytest
from fastapi.testclient import TestClient

# Must be set before importing dashboard module, because install_auth() runs at
# import time and refuses to boot without DASH_PASS.
os.environ["DASH_PASS"] = "test_dashboard_password_long_enough_1234"

from polymarket_weather.api.auth import validate_config_update  # noqa: E402
from polymarket_weather.api.dashboard import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_protected_endpoint_without_key_returns_401(client):
    r = client.get("/api/overview")
    assert r.status_code == 401
    assert "Missing X-API-Key" in r.json()["detail"]


def test_protected_endpoint_with_wrong_key_returns_401(client):
    r = client.get("/api/overview", headers={"X-API-Key": "wrong_value_xyz"})
    assert r.status_code == 401
    assert "Invalid X-API-Key" in r.json()["detail"]


def test_protected_endpoint_with_correct_key_is_allowed(client):
    r = client.get(
        "/api/overview",
        headers={"X-API-Key": os.environ["DASH_PASS"]},
    )
    # 200 or 500 (if DB not wired) — either way, the auth layer let it through.
    # We just need to assert it's NOT 401.
    assert r.status_code != 401


def test_config_put_rejects_unknown_key(client):
    r = client.put(
        "/api/config",
        json={"key": "malicious_injection", "value": "true"},
        headers={"X-API-Key": os.environ["DASH_PASS"]},
    )
    assert r.status_code == 400
    assert "whitelist" in r.json()["detail"]


def test_config_put_rejects_out_of_bounds_value(client):
    r = client.put(
        "/api/config",
        json={"key": "max_position_usdc", "value": "999999"},
        headers={"X-API-Key": os.environ["DASH_PASS"]},
    )
    assert r.status_code == 400


def test_config_put_rejects_non_numeric_for_numeric_key(client):
    r = client.put(
        "/api/config",
        json={"key": "max_position_usdc", "value": "not_a_number"},
        headers={"X-API-Key": os.environ["DASH_PASS"]},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Unit tests for validate_config_update directly
# ---------------------------------------------------------------------------

def test_validate_max_position_in_range():
    assert validate_config_update("max_position_usdc", "25") == 25.0


def test_validate_max_position_out_of_range_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        validate_config_update("max_position_usdc", "10000")
    assert exc_info.value.status_code == 400


def test_validate_unknown_key_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        validate_config_update("paper_trading", "false")  # deliberately excluded
    assert exc_info.value.status_code == 400
    assert "whitelist" in exc_info.value.detail


def test_validate_drawdown_pct_bounds():
    assert validate_config_update("drawdown_pause_pct", "0.15") == 0.15
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        validate_config_update("drawdown_pause_pct", "1.5")  # > 1.0
    with pytest.raises(HTTPException):
        validate_config_update("drawdown_pause_pct", "-0.1")  # < 0.0
