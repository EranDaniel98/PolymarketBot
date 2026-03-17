import pytest
from pathlib import Path
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
