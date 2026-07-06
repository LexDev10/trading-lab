"""`classify_pending_items` contra Postgres real (sección 12.3): persiste
una fila por item pendiente y es idempotente (no reclasifica lo ya
clasificado, regla del almacén PIT — nunca UPDATE). `call_ollama`/
`get_model_version` van mockeados: este test no depende de que el Ollama
real del usuario esté arriba.

# HALLAZGO (2026-07-06, mismo patrón que el incidente documentado en
# docs/PHASE_2_REPORT.md con el paper ledger): estos tests corren contra
# el MISMO Postgres que usa la app real en background (scheduler con
# `fundamental_classify_job` activo). Con el `batch_size` por defecto
# (10), `classify_pending_items` recogía TAMBIÉN noticias reales
# pendientes de clasificar y las contaminaba con la respuesta fake del
# mock — confirmado en vivo (9 filas reales acabaron con
# `model_version="test-version"`, limpiadas a mano). Fix: forzar
# `fundamental_classify_batch_size=1` y usar un `fetched_at` muy anterior
# a cualquier dato real (`NOW`, año 2026 pero anterior a la ingesta real
# de esta sesión) para que el item de test sea SIEMPRE el más antiguo
# (`_pending_news` ordena por `fetched_at` ascendente) y por tanto el
# único elegido — nunca toca el backlog real."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.config import Settings
from core.schemas.fundamental import NewsItem
from db.models import ItemClassification as ItemClassificationRow
from db.models import NewsItem as NewsItemRow
from db.session import get_session
from services.fundamental import classify
from services.fundamental.classify import classify_pending_items
from services.fundamental.ingest_rss import content_hash, persist_news_items

pytestmark = pytest.mark.integration

TEST_SOURCE = "zz_test_classify_source"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def base_settings(**overrides) -> Settings:
    overrides.setdefault("fundamental_classify_batch_size", 1)
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    async with get_session() as session:
        ids = list((await session.execute(select(NewsItemRow.id).where(NewsItemRow.source == TEST_SOURCE))).scalars())
        if ids:
            await session.execute(
                delete(ItemClassificationRow).where(
                    ItemClassificationRow.item_kind == "news", ItemClassificationRow.item_id.in_(ids)
                )
            )
        await session.execute(delete(NewsItemRow).where(NewsItemRow.source == TEST_SOURCE))
        await session.commit()


async def test_classify_pending_items_persists_and_is_idempotent(monkeypatch):
    item = NewsItem(
        source=TEST_SOURCE,
        source_url=None,
        title="Ethereum bridge exploit drains $200M",
        body_text="A major bridge was exploited.",
        asset_tags=["ETH"],
        published_at=NOW,
        fetched_at=NOW,
        content_hash=content_hash(TEST_SOURCE, "1"),
        raw={},
    )
    async with get_session() as session:
        await persist_news_items(session, [item])
        await session.commit()
        news_id = (
            await session.execute(select(NewsItemRow.id).where(NewsItemRow.content_hash == item.content_hash))
        ).scalar_one()

    async def fake_get_model_version(settings: Settings) -> str:
        return "test-version"

    async def fake_call_ollama(settings: Settings, prompt: str) -> dict:
        return {"stance": "bearish_strong", "event_types": ["hack_exploit"], "veto": True, "summary": "exploit"}

    monkeypatch.setattr(classify, "get_model_version", fake_get_model_version)
    monkeypatch.setattr(classify, "call_ollama", fake_call_ollama)

    async with get_session() as session:
        counts_first = await classify_pending_items(session, base_settings(), NOW)
        await session.commit()
    assert counts_first["news"] == 1
    assert counts_first["failed"] == 0

    async with get_session() as session:
        stmt = select(ItemClassificationRow).where(
            ItemClassificationRow.item_kind == "news", ItemClassificationRow.item_id == news_id
        )
        rows = list((await session.execute(stmt)).scalars())
    assert len(rows) == 1
    assert rows[0].stance == "bearish_strong"
    assert rows[0].veto is True
    assert rows[0].asset_tags == ["ETH"]
    assert rows[0].model_version == "test-version"

    # Idempotente: el anti-join de `_pending_news` excluye el item ya
    # clasificado (regla del almacén PIT — nunca UPDATE, sección 12.1).
    # NO se vuelve a llamar a `classify_pending_items` aquí: con un
    # backlog real de producción compartiendo esta misma base de datos
    # (ver el hallazgo de arriba), una segunda pasada del batch
    # inevitablemente clasificaría OTRO item real con la respuesta fake
    # del mock en cuanto el item de test ya no esté pendiente.
    async with get_session() as session:
        pending_ids = [row.id for row in await classify._pending_news(session, limit=1000)]
    assert news_id not in pending_ids


async def test_classify_pending_items_skips_item_on_bad_model_output(monkeypatch):
    item = NewsItem(
        source=TEST_SOURCE,
        source_url=None,
        title="Noticia cualquiera",
        body_text=None,
        asset_tags=["BTC"],
        published_at=NOW,
        fetched_at=NOW,
        content_hash=content_hash(TEST_SOURCE, "2"),
        raw={},
    )
    async with get_session() as session:
        await persist_news_items(session, [item])
        await session.commit()

    async def fake_get_model_version(settings: Settings) -> str:
        return "test-version"

    async def fake_call_ollama_bad(settings: Settings, prompt: str) -> dict:
        return {"stance": "muy_alcista", "event_types": [], "veto": False, "summary": "x"}

    monkeypatch.setattr(classify, "get_model_version", fake_get_model_version)
    monkeypatch.setattr(classify, "call_ollama", fake_call_ollama_bad)

    async with get_session() as session:
        counts = await classify_pending_items(session, base_settings(), NOW)
        await session.commit()

    assert counts["news"] == 0
    assert counts["failed"] == 1
