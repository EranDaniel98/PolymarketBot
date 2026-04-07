"""Tests for polymarket_weather.weather.nwp."""

import pytest
from datetime import datetime, timezone

from polymarket_weather.weather.nwp import EnsembleResult, NwpFetcher, parse_ensemble_response

# ---------------------------------------------------------------------------
# Fixtures / sample data
# ---------------------------------------------------------------------------

SAMPLE_ENSEMBLE = {
    "latitude": 40.64,
    "longitude": -73.78,
    "hourly": {
        "time": ["2026-04-06T00:00", "2026-04-06T01:00", "2026-04-06T02:00"],
        "temperature_2m_member01": [12.0, 11.5, 11.0],
        "temperature_2m_member02": [13.0, 12.5, 12.0],
        "temperature_2m_member03": [11.5, 11.0, 10.5],
    },
}

# ---------------------------------------------------------------------------
# parse_ensemble_response
# ---------------------------------------------------------------------------

def test_parse_ensemble_response():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    assert len(result.times) == 3
    assert result.n_members == 3
    assert result.n_hours == 3
    assert result.lat == 40.64
    assert result.lon == -73.78


def test_parse_timestamps_are_utc():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    for t in result.times:
        assert t.tzinfo is not None, "timestamps must be timezone-aware"
        assert t.tzinfo == timezone.utc


def test_parse_empty_ensemble():
    result = parse_ensemble_response(
        {"hourly": {"time": []}, "latitude": 0, "longitude": 0}
    )
    assert result.n_members == 0
    assert result.n_hours == 0


def test_parse_single_member():
    data = {
        "latitude": 35.0,
        "longitude": 139.0,
        "hourly": {
            "time": ["2026-04-06T12:00"],
            "temperature_2m_member01": [25.5],
        },
    }
    result = parse_ensemble_response(data)
    assert result.n_members == 1
    assert result.mean_at(0) == 25.5


def test_parse_missing_hourly_key():
    """Response with no 'hourly' key should produce an empty result."""
    result = parse_ensemble_response({"latitude": 0.0, "longitude": 0.0})
    assert result.n_members == 0
    assert result.n_hours == 0


def test_parse_members_sorted():
    """Members should be extracted in sorted key order (member01 before member02)."""
    data = {
        "latitude": 0.0,
        "longitude": 0.0,
        "hourly": {
            "time": ["2026-04-06T00:00"],
            "temperature_2m_member02": [20.0],
            "temperature_2m_member01": [10.0],
        },
    }
    result = parse_ensemble_response(data)
    assert result.members[0] == [10.0]
    assert result.members[1] == [20.0]


# ---------------------------------------------------------------------------
# EnsembleResult.mean_at / std_at
# ---------------------------------------------------------------------------

def test_ensemble_mean_at():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    # Hour 0: mean of (12.0, 13.0, 11.5) = 12.1667
    assert abs(result.mean_at(0) - 12.167) < 0.01


def test_ensemble_std_at():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    assert result.std_at(0) > 0


def test_ensemble_std_single_member_returns_none():
    """std with ddof=1 on a single member is undefined → return None (Fix 1.4).

    Previously returned nan, which propagates silently through probability
    math and lets spurious opportunities pass every filter.
    """
    data = {
        "latitude": 0.0,
        "longitude": 0.0,
        "hourly": {
            "time": ["2026-04-06T00:00"],
            "temperature_2m_member01": [15.0],
        },
    }
    result = parse_ensemble_response(data)
    assert result.std_at(0) is None


# ---------------------------------------------------------------------------
# EnsembleResult.at_time
# ---------------------------------------------------------------------------

def test_ensemble_at_time():
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    target = datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc)
    mean, std = result.at_time(target)
    # Hour 1: mean of (11.5, 12.5, 11.0) = 11.6667
    assert abs(mean - 11.667) < 0.01
    assert std > 0


def test_ensemble_at_time_nearest():
    """Should snap to the nearest available hour."""
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    # 00:45 is closer to 01:00 than to 00:00
    target = datetime(2026, 4, 6, 0, 45, tzinfo=timezone.utc)
    mean, _ = result.at_time(target)
    assert abs(mean - 11.667) < 0.01


def test_ensemble_at_time_naive_target():
    """Naive datetime targets should be treated as UTC."""
    result = parse_ensemble_response(SAMPLE_ENSEMBLE)
    target = datetime(2026, 4, 6, 1, 0)  # no tzinfo
    mean, _ = result.at_time(target)
    assert abs(mean - 11.667) < 0.01


def test_ensemble_at_time_empty_raises():
    result = EnsembleResult(times=[], members=[])
    with pytest.raises(ValueError, match="No forecast data"):
        result.at_time(datetime(2026, 4, 6, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# NwpFetcher — unit tests (no network)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetcher_returns_none_before_start():
    fetcher = NwpFetcher(
        api_url="https://api.open-meteo.com/v1/ensemble",
        models=["ecmwf_ifs025"],
    )
    # _http is None before start() is called
    result = await fetcher.fetch_ensemble(40.0, -74.0)
    assert result is None


@pytest.mark.asyncio
async def test_fetcher_deterministic_returns_none_without_config():
    fetcher = NwpFetcher(
        api_url="https://api.open-meteo.com/v1/ensemble",
        models=["ecmwf_ifs025"],
    )
    await fetcher.start()
    try:
        # No deterministic URL configured → should return None
        result = await fetcher.fetch_deterministic(40.0, -74.0)
        assert result is None
    finally:
        await fetcher.stop()


@pytest.mark.asyncio
async def test_fetcher_stop_is_idempotent():
    fetcher = NwpFetcher(
        api_url="https://api.open-meteo.com/v1/ensemble",
        models=["ecmwf_ifs025"],
    )
    await fetcher.start()
    await fetcher.stop()
    # Second stop should not raise
    await fetcher.stop()


@pytest.mark.asyncio
async def test_fetcher_handles_non_200(mocker):
    """Non-200 response should return None without raising."""
    import httpx

    fake_response = mocker.MagicMock(spec=httpx.Response)
    fake_response.status_code = 503
    fake_response.text = "service unavailable"

    url = "https://api.open-meteo.com/v1/ensemble"
    fetcher = NwpFetcher(api_url=url, models=["ecmwf_ifs025"])
    await fetcher.start()
    try:
        mocker.patch.object(fetcher._http, "get", return_value=fake_response)
        # patch get as a coroutine so await works
        async def _fake_get(*args, **kwargs):
            return fake_response

        mocker.patch.object(fetcher._http, "get", side_effect=_fake_get)
        result = await fetcher.fetch_ensemble(40.0, -74.0)
        assert result is None
    finally:
        await fetcher.stop()
