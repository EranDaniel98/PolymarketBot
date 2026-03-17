import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from polymarket_bot.scanner import MarketScanner


@pytest.fixture
def scanner():
    return MarketScanner(max_markets=5)


def _make_response(data):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


async def test_fetch_active_markets_parses_events(scanner):
    data = [
        {
            "title": "US Election 2026",
            "tags": [{"label": "politics"}],
            "markets": [
                {
                    "conditionId": "0xabc123",
                    "question": "Will candidate X win?",
                    "endDate": "2026-11-03T00:00:00Z",
                    "outcomePrices": ["0.65", "0.35"],
                    "clobTokenIds": ["token_yes", "token_no"],
                }
            ],
        }
    ]

    await scanner.start()
    with patch.object(scanner._client, "get", new_callable=AsyncMock, return_value=_make_response(data)):
        markets = await scanner.fetch_active_markets()
        assert len(markets) == 1
        assert markets[0].id == "0xabc123"
        assert markets[0].question == "Will candidate X win?"
        assert markets[0].current_price == 0.65
        assert markets[0].category == "politics"
    await scanner.stop()


async def test_fetch_returns_empty_on_error(scanner):
    await scanner.start()
    with patch.object(scanner._client, "get", new_callable=AsyncMock, side_effect=Exception("API down")):
        markets = await scanner.fetch_active_markets()
        assert markets == []
    await scanner.stop()


async def test_respects_max_markets():
    scanner = MarketScanner(max_markets=2)
    data = [
        {
            "title": f"Event {i}",
            "tags": [],
            "markets": [{"conditionId": f"0x{i}", "question": f"Q{i}?", "clobTokenIds": ["a", "b"]}],
        }
        for i in range(10)
    ]

    await scanner.start()
    with patch.object(scanner._client, "get", new_callable=AsyncMock, return_value=_make_response(data)):
        markets = await scanner.fetch_active_markets()
        assert len(markets) == 2
    await scanner.stop()
