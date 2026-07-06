"""Lecturas sobre `item_classifications` reutilizadas por el resto del
sistema — un solo punto de verdad por pregunta, nunca dos
implementaciones (regla crítica, sección 6):

- `asset_has_active_veto` (sección 12.4, fase 2): "¿hay un veto activo
  para este activo ahora mismo?", usado por `services/scanner/scanner.py`
  (bloquear entradas nuevas) y `services/execution/paper_ledger.py`
  (cierre anticipado de una posición abierta).
- `get_latest_stance` (sección 13, fase 3): "¿cuál es el stance
  fundamental vigente de este activo?", usado por
  `services/decision/policy.py` a través del scanner para fusionar
  técnico × fundamental."""

from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import FundamentalStance
from db.models import ItemClassification as ItemClassificationRow


async def asset_has_active_veto(session: AsyncSession, asset_base: str, settings: Settings, now: datetime) -> bool:
    """`asset_base` es el ticker sin el par de cotización (p.ej. "BTC" de
    "BTCUSDT" — responsabilidad del caller). Un veto está "activo" si hay
    una `item_classifications.veto=true` clasificada dentro de las
    últimas `settings.fundamental_veto_hours` horas cuyo `asset_tags`
    incluye este activo (`# DECISION` sobre la ventana de decaimiento,
    ver `app/config.py::fundamental_veto_hours` — sección 12.4 no la
    define).

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
            ItemClassificationRow.classified_at > cutoff,
            ItemClassificationRow.classified_at <= now,
        )
    )
    result = await session.execute(stmt)
    return bool(result.scalar())


async def get_latest_stance(
    session: AsyncSession, asset_base: str, settings: Settings, now: datetime
) -> FundamentalStance:
    """Stance de la clasificación MÁS RECIENTE que tagea `asset_base`
    dentro de la misma ventana de frescura que el veto
    (`fundamental_veto_hours` — una señal fundamental "caduca" igual sea
    para bloquear o para informar, no hay motivo para dos ventanas
    distintas). `unknown` si no hay ninguna dentro de la ventana — mismo
    valor que usaría el clasificador para "sin dirección clara", así que
    la tabla de política (sección 13) lo trata igual que si nunca se
    hubiera clasificado nada.

    `classified_at <= now`: mismo principio anti look-ahead que
    `asset_has_active_veto` (ver ahí el porqué)."""
    cutoff = now - timedelta(hours=settings.fundamental_veto_hours)
    stmt = (
        select(ItemClassificationRow.stance)
        .where(
            ItemClassificationRow.asset_tags.contains([asset_base]),
            ItemClassificationRow.classified_at > cutoff,
            ItemClassificationRow.classified_at <= now,
        )
        .order_by(ItemClassificationRow.classified_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    stance = result.scalar_one_or_none()
    return FundamentalStance(stance) if stance is not None else FundamentalStance.unknown
