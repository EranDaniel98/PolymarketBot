"""One-shot: backfill event_slug for trades that already have an event_id.

Looks up each unique event_id from the trades table on Polymarket's Gamma
API (/events/{id}), pulls the slug, and writes it back to the trades.event_slug
column. Idempotent — only updates rows where event_slug is null/empty.

Run via: railway run python scripts/backfill_event_slugs.py
(needs DATABASE_URL in the env, which Railway injects automatically.)
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def fetch_event_slug(client: httpx.AsyncClient, event_id: str) -> str | None:
    """Look up an event by id on Gamma and return its slug."""
    try:
        r = await client.get(
            f"https://gamma-api.polymarket.com/events/{event_id}",
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            slug = data.get("slug")
            return str(slug) if slug else None
    except Exception as exc:
        print(f"  fetch failed for event {event_id}: {exc}", file=sys.stderr)
    return None


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgresql://"):
        db_url = "postgresql+asyncpg://" + db_url[len("postgresql://"):]
    elif db_url.startswith("postgres://"):
        db_url = "postgresql+asyncpg://" + db_url[len("postgres://"):]
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as session:
        # Find every distinct event_id with no slug yet — raw SQL to avoid
        # importing the ORM model (script may run from any cwd).
        result = await session.execute(
            sa.text(
                "SELECT DISTINCT event_id FROM trades "
                "WHERE event_id IS NOT NULL "
                "AND (event_slug IS NULL OR event_slug = '')"
            )
        )
        event_ids = [row[0] for row in result.all() if row[0]]

    print(f"Found {len(event_ids)} unique event_ids needing slug backfill")
    if not event_ids:
        await engine.dispose()
        return 0

    async with httpx.AsyncClient() as client:
        slug_by_event: dict[str, str] = {}
        for eid in event_ids:
            slug = await fetch_event_slug(client, eid)
            if slug:
                slug_by_event[eid] = slug
                print(f"  {eid} → {slug}")
            else:
                print(f"  {eid} → (no slug)")

    if not slug_by_event:
        print("Nothing to update")
        await engine.dispose()
        return 0

    async with sf() as session:
        for eid, slug in slug_by_event.items():
            await session.execute(
                sa.text(
                    "UPDATE trades SET event_slug = :slug "
                    "WHERE event_id = :eid "
                    "AND (event_slug IS NULL OR event_slug = '')"
                ),
                {"slug": slug, "eid": eid},
            )
        await session.commit()

    print(f"Updated {len(slug_by_event)} events across all matching trades")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
