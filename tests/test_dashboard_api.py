"""Tests for FastAPI dashboard endpoints."""
import pytest
from fastapi.testclient import TestClient
from polymarket_weather.api.dashboard import app, set_state


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_overview_default(client):
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_pnl" in data
    assert "paper_mode" in data


def test_opportunities_empty(client):
    resp = client.get("/api/opportunities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_positions_empty(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_positions_with_data(client):
    from polymarket_weather.trading.positions import PositionManager
    pm = PositionManager()
    pm.track_entry("0xabc", "YES", 0.55, 25.0, "nyc", "evt_1")
    set_state(positions=pm)
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["market_id"] == "0xabc"
    assert data[0]["direction"] == "YES"
    set_state(positions=None)


def test_history_empty(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_weather_empty(client):
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    assert resp.json() == []


def test_calibration_empty(client):
    resp = client.get("/api/calibration")
    assert resp.status_code == 200
    assert resp.json() == []


def test_config_empty(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == []


def test_cities_empty(client):
    resp = client.get("/api/cities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_events_empty(client):
    resp = client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_config_update_no_db(client):
    resp = client.put("/api/config", json={"key": "test", "value": "123"})
    assert resp.status_code == 503
