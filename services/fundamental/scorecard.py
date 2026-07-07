"""Scorecard semanal del clasificador (sección 12.3/16): hit-rate y
retorno medio de cada `stance` contra retornos realizados a 4h/24h/72h —
"esto es lo que decide si la capa aporta señal" (sección 12.3). Corre una
vez por semana (`app/scheduler.py::classifier_scorecard_job`), sobre la
semana que acaba de cerrar.

# DECISION (2026-07-06, no está en el spec): "hit" solo tiene sentido para
# stances direccionales. `neutral` y `unknown` no hacen una predicción de
# dirección, así que se excluyen del hit-rate (igual criterio para ambos,
# no solo para `unknown` como decía el plan inicial). Para
# `bullish_*`: hit si el retorno futuro > 0. Para `bearish_*`: hit si el
# retorno futuro < 0. `avg_fwd_return` usa el retorno FIRMADO según el
# acierto (positivo = en la dirección correcta) para que el signo sea
# comparable entre stances bullish y bearish.
#
# Con cero clasificaciones todavía (clasificador recién construido), el
# scorecard queda vacío hasta que se acumulen datos — no es un bug.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.market import Candle
from db.models import ClassifierScorecard as ClassifierScorecardRow
from db.models import ItemClassification as ItemClassificationRow
from services.data.persistence import get_all_candles

HORIZONS: dict[str, timedelta] = {"4h": timedelta(hours=4), "24h": timedelta(hours=24), "72h": timedelta(hours=72)}

BULLISH_PREFIX = "bullish"
BEARISH_PREFIX = "bearish"


def _week_start(now: datetime) -> datetime:
    """Lunes 00:00 UTC de la semana de `now`."""
    floored = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return floored - timedelta(days=floored.weekday())


def _price_at_or_before(candles: list[Candle], ts: datetime) -> Decimal | None:
    """`candles` ascendente por `open_time`. Devuelve el `close` de la
    última vela cerrada en o antes de `ts` (anti look-ahead: nunca una
    vela posterior)."""
    price = None
    for candle in candles:
        if candle.open_time > ts:
            break
        price = candle.close
    return price


def _hit(stance: str, fwd_return: Decimal) -> tuple[bool, Decimal] | None:
    """Devuelve `(hit, retorno_firmado)` o `None` si el stance no es
    direccional (neutral/unknown, ver `# DECISION` arriba)."""
    if stance.startswith(BULLISH_PREFIX):
        return fwd_return > 0, fwd_return
    if stance.startswith(BEARISH_PREFIX):
        return fwd_return < 0, -fwd_return
    return None


async def compute_weekly_scorecard(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(tz=UTC)
    this_week_start = _week_start(now)
    last_week_start = this_week_start - timedelta(days=7)

    stmt = select(ItemClassificationRow).where(
        ItemClassificationRow.classified_at >= last_week_start,
        ItemClassificationRow.classified_at < this_week_start,
    )
    result = await session.execute(stmt)
    classifications = list(result.scalars().all())

    # buckets[(stance, horizon)] = lista de (hit: bool, retorno_firmado)
    buckets: dict[tuple[str, str], list[tuple[bool, Decimal]]] = defaultdict(list)
    candles_cache: dict[str, list[Candle]] = {}

    for item in classifications:
        if not item.asset_tags:
            continue
        # FIX (2026-07-07, bug #14 CODE_REVIEW_2026-07-07.md, punto 3):
        # iterar TODOS los `asset_tags`, no solo `asset_tags[0]` — un item
        # puede afectar a más de un activo a la vez (p.ej. BTC y ETH).
        for tag in item.asset_tags:
            asset = f"{tag}USDT"
            if asset not in candles_cache:
                candles_cache[asset] = await get_all_candles(session, asset, "1h")
            candles = candles_cache[asset]
            if not candles:
                continue

            price_start = _price_at_or_before(candles, item.classified_at)
            if price_start is None or price_start <= 0:
                continue

            last_available_open_time = candles[-1].open_time
            for horizon_label, horizon_delta in HORIZONS.items():
                target_ts = item.classified_at + horizon_delta
                if last_available_open_time < target_ts:
                    # FIX (2026-07-07, bug #14, punto 1): sin vela
                    # suficiente para ESTE horizonte todavía — antes se
                    # usaba la última vela disponible como si fuera el
                    # retorno "a 72h" aunque solo hubieran pasado 12h
                    # reales, falseando el horizonte. Se descarta el
                    # punto hasta que exista dato real a esa distancia.
                    continue
                price_end = _price_at_or_before(candles, target_ts)
                if price_end is None:
                    continue
                fwd_return = (price_end - price_start) / price_start
                scored = _hit(item.stance, fwd_return)
                if scored is None:
                    continue
                buckets[(item.stance, horizon_label)].append(scored)

    for (stance, horizon), points in buckets.items():
        n = len(points)
        hits = sum(1 for hit, _ in points if hit)
        hit_rate = Decimal(hits) / Decimal(n)
        avg_fwd_return = sum((signed for _, signed in points), Decimal("0")) / n
        # FIX (2026-07-07, bug #14, punto 2): upsert por `(week, stance,
        # horizon)` en vez de INSERT puro — re-ejecutar el job de la
        # misma semana (reintento manual, redeploy) ya no duplica filas.
        # `classifier_scorecard` es una tabla DERIVADA/recalculable
        # (única excepción a la regla append-only del almacén PIT, que
        # solo aplica a `news_items`/`social_items`/`item_classifications`
        # — datos primarios point-in-time, no a este agregado).
        stmt = pg_insert(ClassifierScorecardRow).values(
            week=last_week_start.date(),
            stance=stance,
            horizon=horizon,
            n=n,
            hit_rate=hit_rate,
            avg_fwd_return=avg_fwd_return,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                ClassifierScorecardRow.week,
                ClassifierScorecardRow.stance,
                ClassifierScorecardRow.horizon,
            ],
            set_={"n": n, "hit_rate": hit_rate, "avg_fwd_return": avg_fwd_return},
        )
        await session.execute(stmt)

    return {"classifications_considered": len(classifications), "scorecard_rows": len(buckets)}
