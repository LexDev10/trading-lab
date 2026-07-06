"""`services/fundamental/veto.py` contra Postgres real:
`asset_has_active_veto` (sección 12.4) — filtro por `veto=true`,
`asset_tags` (operador `@>` de Postgres) y ventana de decaimiento
(`fundamental_veto_hours`); `get_latest_stance` (sección 13) — misma
ventana, devuelve el stance más reciente o `unknown`."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.config import Settings
from core.enums import FundamentalStance
from db.models import ItemClassification as ItemClassificationRow
from db.session import get_session
from services.fundamental.veto import asset_has_active_veto, get_latest_stance

pytestmark = pytest.mark.integration

TEST_MODEL = "zz_test_veto_model"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def base_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    async with get_session() as session:
        await session.execute(delete(ItemClassificationRow).where(ItemClassificationRow.model_name == TEST_MODEL))
        await session.commit()


def _classification(**overrides) -> ItemClassificationRow:
    defaults: dict = dict(
        item_id=1,
        item_kind="news",
        model_name=TEST_MODEL,
        model_version="v1",
        classified_at=NOW,
        stance="bearish_strong",
        event_types=["hack_exploit"],
        veto=True,
        summary="exploit grande",
        output_jsonb={},
        asset_tags=["BTC"],
    )
    defaults.update(overrides)
    return ItemClassificationRow(**defaults)


async def test_active_veto_detected_within_window():
    async with get_session() as session:
        session.add(_classification())
        await session.commit()

    async with get_session() as session:
        active = await asset_has_active_veto(session, "BTC", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=1))
    assert active is True


async def test_veto_inactive_for_a_different_asset():
    async with get_session() as session:
        session.add(_classification(asset_tags=["BTC"]))
        await session.commit()

    async with get_session() as session:
        active = await asset_has_active_veto(session, "ETH", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=1))
    assert active is False


async def test_veto_expires_after_configured_hours():
    async with get_session() as session:
        session.add(_classification())
        await session.commit()

    async with get_session() as session:
        active = await asset_has_active_veto(session, "BTC", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=25))
    assert active is False


async def test_non_veto_classification_does_not_block():
    async with get_session() as session:
        session.add(_classification(veto=False))
        await session.commit()

    async with get_session() as session:
        active = await asset_has_active_veto(session, "BTC", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=1))
    assert active is False


async def test_get_latest_stance_returns_most_recent_within_window():
    async with get_session() as session:
        session.add(_classification(item_id=2, classified_at=NOW, stance="bearish_weak", veto=False))
        session.add(
            _classification(
                item_id=3, classified_at=NOW + timedelta(hours=2), stance="bullish_strong", veto=False
            )
        )
        await session.commit()

    async with get_session() as session:
        stance = await get_latest_stance(
            session, "BTC", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=3)
        )
    assert stance == FundamentalStance.bullish_strong


async def test_get_latest_stance_unknown_without_any_classification():
    async with get_session() as session:
        stance = await get_latest_stance(session, "BTC", base_settings(fundamental_veto_hours=24), NOW)
    assert stance == FundamentalStance.unknown


async def test_get_latest_stance_unknown_once_expired():
    async with get_session() as session:
        session.add(_classification(item_id=4, classified_at=NOW, stance="bullish_strong", veto=False))
        await session.commit()

    async with get_session() as session:
        stance = await get_latest_stance(
            session, "BTC", base_settings(fundamental_veto_hours=24), NOW + timedelta(hours=25)
        )
    assert stance == FundamentalStance.unknown
