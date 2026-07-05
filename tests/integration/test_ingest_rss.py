"""Integration test contra Postgres real: `persist_news_items` respeta la
regla de la sección 12.1 (almacén PIT append-only, idempotente por
`content_hash`, nunca UPDATE) — sin red, con `NewsItem` ya construidos a
mano."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from core.schemas.fundamental import NewsItem
from db.models import NewsItem as NewsItemRow
from db.session import get_session
from services.fundamental.ingest_rss import content_hash, persist_news_items

pytestmark = pytest.mark.integration

TEST_SOURCE = "zz_test_source"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    async with get_session() as session:
        await session.execute(delete(NewsItemRow).where(NewsItemRow.source == TEST_SOURCE))
        await session.commit()


def _item(external_id: str, title: str) -> NewsItem:
    return NewsItem(
        source=TEST_SOURCE,
        source_url=f"https://example.com/{external_id}",
        title=title,
        body_text=None,
        asset_tags=["BTC"],
        published_at=NOW,
        fetched_at=NOW,
        content_hash=content_hash(TEST_SOURCE, external_id),
        raw={"id": external_id},
    )


@pytest.mark.asyncio
async def test_persist_news_items_is_idempotent():
    items = [_item("1", "Item one"), _item("2", "Item two")]

    async with get_session() as session:
        inserted_first = await persist_news_items(session, items)
        await session.commit()
    assert inserted_first == 2

    async with get_session() as session:
        inserted_second = await persist_news_items(session, items)
        await session.commit()
    assert inserted_second == 0  # ya existían -> ON CONFLICT DO NOTHING, no duplica

    async with get_session() as session:
        result = await session.execute(select(NewsItemRow).where(NewsItemRow.source == TEST_SOURCE))
        rows = result.scalars().all()
    assert len(rows) == 2
