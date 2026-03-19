import pytest
from unittest.mock import AsyncMock, MagicMock
from polymarket_bot.data_sources.metaculus import MetaculusClient


@pytest.fixture
def client():
    c = MetaculusClient(cache_ttl=300)
    c._http = AsyncMock()
    return c


async def test_search_parses_results(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "Will AI pass Turing test?",
                "community_prediction": {"full": {"q2": 0.35}},
                "number_of_forecasters": 150,
                "url": "https://metaculus.com/q/1",
            },
            {
                "title": "Another question",
                "community_prediction": {"full": {}},
                "number_of_forecasters": 20,
                "url": "",
            },
        ],
    }
    client._http.get.return_value = mock_resp

    results = await client.search("AI Turing test")
    assert len(results) == 2
    assert results[0].community_prediction == 0.35
    assert results[0].forecaster_count == 150
    assert results[1].community_prediction is None


async def test_search_caching(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}
    client._http.get.return_value = mock_resp

    await client.search("test query")
    await client.search("test query")  # Should use cache

    assert client._http.get.call_count == 1


async def test_format_for_llm(client):
    from polymarket_bot.data_sources.metaculus import MetaculusForecast
    forecasts = [
        MetaculusForecast("Q1", 0.75, 100, "url1"),
        MetaculusForecast("Q2", None, 20, "url2"),
    ]
    text = client.format_for_llm(forecasts)
    assert "community=75%" in text
    assert "100 forecasters" in text
    assert "20 forecasters" in text
