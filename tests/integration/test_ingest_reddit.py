"""Integration test contra Postgres real: `persist_social_items` respeta
la regla de la sección 12.1 (almacén PIT append-only, idempotente por
`post_id`, nunca UPDATE) — sin red, con `SocialItem` ya construidos a
mano. Mismo patrón que `tests/integration/test_ingest_rss.py`."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from core.schemas.fundamental import SocialItem
from db.models import SocialItem as SocialItemRow
from db.session import get_session
from services.fundamental.ingest_reddit import persist_social_items

pytestmark = pytest.mark.integration

TEST_SUBREDDIT = "zz_test_subreddit"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    async with get_session() as session:
        await session.execute(delete(SocialItemRow).where(SocialItemRow.subreddit == TEST_SUBREDDIT))
        await session.commit()


def _item(post_id: str, title: str) -> SocialItem:
    return SocialItem(
        platform="reddit",
        subreddit=TEST_SUBREDDIT,
        post_id=post_id,
        title=title,
        body_text=None,
        score_at_fetch=10,
        num_comments_at_fetch=2,
        published_at=NOW,
        fetched_at=NOW,
        raw={"id": post_id},
    )


@pytest.mark.asyncio
async def test_persist_social_items_is_idempotent():
    items = [_item("post1", "Item one"), _item("post2", "Item two")]

    async with get_session() as session:
        inserted_first = await persist_social_items(session, items)
        await session.commit()
    assert inserted_first == 2

    async with get_session() as session:
        inserted_second = await persist_social_items(session, items)
        await session.commit()
    assert inserted_second == 0  # ya existían -> ON CONFLICT DO NOTHING, no duplica

    async with get_session() as session:
        result = await session.execute(select(SocialItemRow).where(SocialItemRow.subreddit == TEST_SUBREDDIT))
        rows = result.scalars().all()
    assert len(rows) == 2
