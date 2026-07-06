"""Clasificador Ollama (sección 12.3, fase 2): convierte cada
`NewsItem`/`SocialItem` sin clasificar en una fila de
`item_classifications` (`stance`, `event_types`, `veto`, `summary`).

# DECISION (2026-07-06): probado el `format` de JSON Schema estricto de
# Ollama (`/api/chat` con `format: {...schema...}`) contra el Ollama real
# del usuario (v0.31.1, `qwen3.5:9b`) — el modelo lo ignora por completo
# y devuelve prosa libre, incluso con `think: false`. En cambio, `format:
# "json"` (modo JSON suelto, sin schema) SÍ funciona de forma fiable si
# el prompt enumera explícitamente los valores válidos de cada campo
# (verificado con dos casos reales, incluido un veto correctamente
# detectado). Se usa `format: "json"` + validación estricta con Pydantic
# del lado Python (`ParsedClassification`): un valor fuera de enum lanza
# `ValidationError`, que el caller trata como fallo de ESE item — más
# robusto que depender de que el grammar-constraint de Ollama funcione
# para cualquier modelo que el usuario decida usar más adelante.
#
# Nunca se hace UPDATE (regla del almacén PIT, sección 12.1): reclasificar
# con otro modelo/prompt es una fila nueva en `item_classifications`.
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import EventType, FundamentalStance
from core.logging import get_logger
from db.models import ItemClassification as ItemClassificationRow
from db.models import NewsItem as NewsItemRow
from db.models import SocialItem as SocialItemRow
from services.fundamental.ingest_reddit import ASSET_SUBREDDITS

logger = get_logger("classify")

ItemKind = Literal["news", "social"]

# Mapeo inverso subreddit (minúsculas) -> base ticker. El subreddit
# genérico `CryptoCurrency` no está aquí a propósito: sentimiento de
# mercado general, no específico de un activo — un veto debe ser
# accionable solo sobre el activo que realmente lo motiva.
_SUBREDDIT_TO_ASSET = {subreddit.lower(): base for base, subreddit in ASSET_SUBREDDITS.items()}

PROMPT_TEMPLATE = """Eres un clasificador de noticias/posts cripto para un sistema de trading sistemático, sin sesgo ni especulación narrativa. Responde SOLO con un objeto JSON (sin texto adicional, sin markdown, sin explicación) con exactamente estas claves:

- "stance": exactamente uno de: {stances}
- "event_types": lista (puede ser vacía) con cero o más de: {event_types}
- "veto": true SOLO si el contenido es lo bastante grave (hackeo/exploit grande, delisting, acción regulatoria severa, insolvencia) como para justificar NO abrir posiciones nuevas en el/los activo(s) afectado(s) ahora mismo; false en cualquier otro caso (incluidas noticias positivas o neutras).
- "summary": resumen de una sola frase, en español.

{asset_line}Contenido a clasificar:
Título: {title}
Cuerpo: {body}
"""


def build_prompt(title: str, body_text: str | None, asset_tags: list[str]) -> str:
    asset_line = f"Activo(s) relacionados: {', '.join(asset_tags)}.\n" if asset_tags else ""
    return PROMPT_TEMPLATE.format(
        stances=", ".join(s.value for s in FundamentalStance),
        event_types=", ".join(e.value for e in EventType),
        asset_line=asset_line,
        title=title,
        body=(body_text or "")[:2000],
    )


class ParsedClassification(BaseModel):
    stance: FundamentalStance
    event_types: list[EventType]
    veto: bool
    summary: str


async def call_ollama(settings: Settings, prompt: str) -> dict[str, Any]:
    payload = {
        "model": settings.ollama_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{settings.ollama_host}/api/chat", json=payload)
        response.raise_for_status()
    content = response.json()["message"]["content"]
    result: dict[str, Any] = json.loads(content)
    return result


async def get_model_version(settings: Settings) -> str | None:
    """Digest del modelo configurado (sección 12.3: "registrar
    model_name+version en cada clasificación") — leído de `/api/tags` en
    vez de hardcodear una versión, para detectar si el usuario actualiza
    el modelo sin cambiar el tag. `None` si el modelo no está descargado
    en el Ollama del host (fail-closed: no se clasifica nada ese run)."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{settings.ollama_host}/api/tags")
        response.raise_for_status()
    for model in response.json().get("models", []):
        if model.get("name") == settings.ollama_model or model.get("model") == settings.ollama_model:
            digest = str(model.get("digest", ""))
            return digest[:12] if digest else None
    return None


def resolve_asset_tags(
    item_kind: ItemKind, news_row: NewsItemRow | None = None, social_row: SocialItemRow | None = None
) -> list[str]:
    if item_kind == "news":
        assert news_row is not None
        return list(news_row.asset_tags)
    assert social_row is not None
    base = _SUBREDDIT_TO_ASSET.get(social_row.subreddit.lower())
    return [base] if base else []


async def _pending_news(session: AsyncSession, limit: int) -> list[NewsItemRow]:
    already_classified = exists().where(
        ItemClassificationRow.item_kind == "news", ItemClassificationRow.item_id == NewsItemRow.id
    )
    stmt = select(NewsItemRow).where(~already_classified).order_by(NewsItemRow.fetched_at.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _pending_social(session: AsyncSession, limit: int) -> list[SocialItemRow]:
    already_classified = exists().where(
        ItemClassificationRow.item_kind == "social", ItemClassificationRow.item_id == SocialItemRow.id
    )
    stmt = select(SocialItemRow).where(~already_classified).order_by(SocialItemRow.fetched_at.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _classify_and_persist(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
    model_version: str,
    *,
    item_kind: ItemKind,
    item_id: int,
    title: str,
    body_text: str | None,
    asset_tags: list[str],
) -> bool:
    """Fail-closed por item (mismo criterio que `ingest_rss.ingest_all`
    fail-closed por fuente): cualquier fallo (red, JSON inválido, enum
    fuera de esquema) se loguea y se salta ESTE item, sin tumbar el
    batch."""
    try:
        prompt = build_prompt(title, body_text, asset_tags)
        raw = await call_ollama(settings, prompt)
        parsed = ParsedClassification.model_validate(raw)
    except Exception:
        logger.exception("classify.item_failed", item_kind=item_kind, item_id=item_id)
        return False

    session.add(
        ItemClassificationRow(
            item_id=item_id,
            item_kind=item_kind,
            model_name=settings.ollama_model,
            model_version=model_version,
            classified_at=now,
            stance=parsed.stance.value,
            event_types=[e.value for e in parsed.event_types],
            veto=parsed.veto,
            summary=parsed.summary,
            output_jsonb=raw,
            asset_tags=asset_tags,
        )
    )
    return True


async def classify_pending_items(session: AsyncSession, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Clasifica hasta `settings.fundamental_classify_batch_size` items
    pendientes en total (news primero, luego social con el presupuesto
    restante) — acota la duración del job, ya que cada llamada a Ollama
    tarda varios segundos."""
    now = now or datetime.now(tz=UTC)
    counts = {"news": 0, "social": 0, "failed": 0}

    try:
        model_version = await get_model_version(settings)
    except Exception:
        logger.exception("classify.model_version_failed")
        return counts
    if model_version is None:
        logger.warning("classify.model_not_available", model=settings.ollama_model)
        return counts

    news_pending = await _pending_news(session, settings.fundamental_classify_batch_size)
    for item in news_pending:
        asset_tags = resolve_asset_tags("news", news_row=item)
        ok = await _classify_and_persist(
            session, settings, now, model_version,
            item_kind="news", item_id=item.id, title=item.title, body_text=item.body_text, asset_tags=asset_tags,
        )
        counts["news" if ok else "failed"] += 1

    remaining = max(settings.fundamental_classify_batch_size - len(news_pending), 0)
    if remaining:
        social_pending = await _pending_social(session, remaining)
        for social_item in social_pending:
            asset_tags = resolve_asset_tags("social", social_row=social_item)
            ok = await _classify_and_persist(
                session, settings, now, model_version,
                item_kind="social", item_id=social_item.id, title=social_item.title,
                body_text=social_item.body_text, asset_tags=asset_tags,
            )
            counts["social" if ok else "failed"] += 1

    return counts
