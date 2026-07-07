"""Lecturas sobre `item_classifications` reutilizadas por el resto del
sistema — un solo punto de verdad por pregunta, nunca dos
implementaciones (regla crítica, sección 6):

- `asset_has_active_veto` (sección 12.4, fase 2): "¿hay un veto activo
  para este activo ahora mismo?", usado por `services/scanner/scanner.py`
  para BLOQUEAR ENTRADAS NUEVAS. Cualquier fuente (news o social) lo
  activa — es una salvaguarda de riesgo, más vale bloquear una entrada de
  más que abrir sobre una noticia grave.
- `asset_has_active_closing_veto` (fase 2, FIX 2026-07-07 bug #18): "¿debe
  CERRARSE una posición ABIERTA ahora mismo?", usado por
  `services/execution/paper_ledger.py` (cierre anticipado). A diferencia
  de la anterior, exige `item_kind='news'` y corroboración de
  `settings.fundamental_veto_min_sources` fuentes independientes DISTINTAS
  dentro de la ventana.

  # DECISION (2026-07-07, bug #18 CODE_REVIEW_2026-07-07.md): un LLM
  # clasificando contenido de Reddit (no autenticado, prompt injection
  # trivial: un post redactado para que el modelo devuelva `veto: true`
  # sobre un activo) no debe poder forzar el cierre de una posición real
  # por sí solo — el spec dice "ningún LLM en el camino señal→orden" y
  # aunque la salida sea categórica, un cierre forzoso ES una acción con
  # impacto económico directo. `social` sigue bloqueando entradas NUEVAS
  # (más conservador ahí no tiene coste real: en el peor caso se pierde
  # una oportunidad), pero ya no cierra posiciones sin corroboración de
  # noticias independientes.

- `get_latest_stance` (sección 13, fase 3): "¿cuál es el stance
  fundamental vigente de este activo?", usado por
  `services/decision/policy.py` a través del scanner para fusionar
  técnico × fundamental."""

from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import FundamentalStance
from db.models import ItemClassification as ItemClassificationRow

# FIX (2026-07-07, bug #18): la ventana de decaimiento se mide desde
# `published_at` (cuándo ocurrió/publicó realmente el evento) con
# fallback a `classified_at` cuando `published_at` es NULL (filas
# antiguas sin backfill, o fuentes sin fecha de publicación fiable) —
# antes se medía siempre desde `classified_at`: un backlog viejo
# clasificado tarde generaba vetos "frescos" de noticias de hace días.
_EFFECTIVE_TS = func.coalesce(ItemClassificationRow.published_at, ItemClassificationRow.classified_at)


async def asset_has_active_veto(session: AsyncSession, asset_base: str, settings: Settings, now: datetime) -> bool:
    """`asset_base` es el ticker sin el par de cotización (p.ej. "BTC" de
    "BTCUSDT" — responsabilidad del caller). Un veto está "activo" si hay
    una `item_classifications.veto=true` clasificada dentro de las
    últimas `settings.fundamental_veto_hours` horas (medidas desde
    `published_at`, ver `_EFFECTIVE_TS`) cuyo `asset_tags` incluye este
    activo. Bloquea ENTRADAS NUEVAS — cualquier fuente cuenta, ver
    docstring del módulo sobre por qué el cierre de posiciones abiertas
    usa un criterio distinto (`asset_has_active_closing_veto`).

    `classified_at <= now` es tan importante como el propio filtro de
    ventana: mismo principio anti look-ahead que el resto del sistema
    (sección 12.1, "toda consulta usa fetched_at <= momento_de_decisión")
    — sin este límite superior, una clasificación con `classified_at`
    posterior a `now` (backtesting, jobs retrasados, o simplemente datos
    de otra ventana temporal) contaría como si ya se conociera en el
    momento de la decisión."""
    cutoff = now - timedelta(hours=settings.fundamental_veto_hours)
    stmt = select(
        exists().where(
            ItemClassificationRow.veto.is_(True),
            ItemClassificationRow.asset_tags.contains([asset_base]),
            _EFFECTIVE_TS > cutoff,
            _EFFECTIVE_TS <= now,
            ItemClassificationRow.classified_at <= now,
        )
    )
    result = await session.execute(stmt)
    return bool(result.scalar())


async def asset_has_active_closing_veto(session: AsyncSession, asset_base: str, settings: Settings, now: datetime) -> bool:
    """FIX (2026-07-07, bug #18): cierre forzoso de una posición ABIERTA —
    criterio más estricto que `asset_has_active_veto`. Solo cuenta
    `item_kind='news'` (nunca `social`, ver docstring del módulo) y exige
    al menos `settings.fundamental_veto_min_sources` valores DISTINTOS de
    `source` con `veto=true` dentro de la ventana (corroboración de
    fuentes independientes, no repetición de la misma fuente)."""
    cutoff = now - timedelta(hours=settings.fundamental_veto_hours)
    stmt = select(ItemClassificationRow.source).where(
        ItemClassificationRow.veto.is_(True),
        ItemClassificationRow.item_kind == "news",
        ItemClassificationRow.asset_tags.contains([asset_base]),
        _EFFECTIVE_TS > cutoff,
        _EFFECTIVE_TS <= now,
        ItemClassificationRow.classified_at <= now,
    )
    result = await session.execute(stmt)
    distinct_sources = {source for (source,) in result.all() if source}
    return len(distinct_sources) >= settings.fundamental_veto_min_sources


async def get_latest_stance(
    session: AsyncSession, asset_base: str, settings: Settings, now: datetime
) -> FundamentalStance:
    """Stance de la clasificación MÁS RECIENTE que tagea `asset_base`
    dentro de la misma ventana de frescura que el veto
    (`fundamental_veto_hours`, medida desde `published_at` — ver
    `_EFFECTIVE_TS` — una señal fundamental "caduga" igual sea para
    bloquear o para informar, no hay motivo para dos ventanas distintas).
    `unknown` si no hay ninguna dentro de la ventana — mismo valor que
    usaría el clasificador para "sin dirección clara", así que la tabla
    de política (sección 13) lo trata igual que si nunca se hubiera
    clasificado nada.

    `classified_at <= now`: mismo principio anti look-ahead que
    `asset_has_active_veto` (ver ahí el porqué)."""
    cutoff = now - timedelta(hours=settings.fundamental_veto_hours)
    stmt = (
        select(ItemClassificationRow.stance)
        .where(
            ItemClassificationRow.asset_tags.contains([asset_base]),
            _EFFECTIVE_TS > cutoff,
            _EFFECTIVE_TS <= now,
            ItemClassificationRow.classified_at <= now,
        )
        .order_by(_EFFECTIVE_TS.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    stance = result.scalar_one_or_none()
    return FundamentalStance(stance) if stance is not None else FundamentalStance.unknown
