import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
from polymarket_bot.resolution_tracker import ResolutionTracker


@pytest.fixture
async def mock_db():
    db = AsyncMock()
    db.get_unresolved_market_ids.return_value = ["m1", "m2"]
    return db


@pytest.fixture
def tracker(mock_db):
    return ResolutionTracker(database=mock_db, poll_interval=60)


async def test_check_resolutions_calls_fetch(tracker, mock_db):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"outcome": "Yes"}

    tracker._http = AsyncMock()
    tracker._http.get.return_value = mock_response

    await tracker._check_resolutions()

    assert mock_db.record_resolution.call_count == 2
    mock_db.record_resolution.assert_any_call("m1", "Yes")
    mock_db.record_resolution.assert_any_call("m2", "Yes")


async def test_fetch_outcome_returns_none_for_unresolved(tracker):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "active"}

    tracker._http = AsyncMock()
    tracker._http.get.return_value = mock_response

    result = await tracker._fetch_outcome("m1")
    assert result is None


async def test_fetch_outcome_returns_none_on_error(tracker):
    tracker._http = AsyncMock()
    tracker._http.get.side_effect = Exception("network error")

    result = await tracker._fetch_outcome("m1")
    assert result is None


async def test_no_unresolved_markets(tracker, mock_db):
    mock_db.get_unresolved_market_ids.return_value = []
    tracker._http = AsyncMock()

    await tracker._check_resolutions()
    tracker._http.get.assert_not_called()
