"""APScheduler in-process (sección 5: sin Celery, sin colas externas —
swing corto no las necesita). Tres jobs independientes, cada uno con su
propio dominio de fallo:
- `market_cycle_job`, cada `SCAN_INTERVAL_MINUTES`: ingesta + scan + paper
  ledger (el core del sistema).
- `fundamental_ingest_job`, misma cadencia: ingesta RSS/JSON al almacén
  PIT (sección 12.2) — un fallo aquí no debe tumbar el ciclo técnico.
- `daily_summary_job`, cron 22:00 UTC: resumen diario por Telegram
  (sección 17)."""

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from core.logging import get_logger
from db.session import get_session
from notifications.telegram import send_message
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import insert_market_snapshots, upsert_asset, upsert_candles
from services.execution.paper_ledger import update_open_positions
from services.fundamental import ingest_reddit, ingest_rss
from services.reporting.daily_summary import build_daily_summary
from services.scanner.scanner import run_scan_cycle

logger = get_logger("scheduler")

TIMEFRAMES = ("1h", "4h")


async def _ingest_market_data(now: datetime) -> None:
    settings = get_settings()
    client = BinanceMarketData()
    log = logger.bind(job="ingest_market_data", started_at=now.isoformat())
    log.info("job.start")

    async with get_session() as session:
        for asset in settings.universe_list:
            await upsert_asset(session, asset)
            for timeframe in TIMEFRAMES:
                candles = await asyncio.to_thread(client.fetch_klines, asset, timeframe, 500)
                await upsert_candles(session, candles)

        snapshots = await asyncio.to_thread(client.fetch_ticker_24h, settings.universe_list)
        await insert_market_snapshots(session, snapshots)
        await session.commit()

    log.info("job.success", assets=len(settings.universe_list))


async def _scan_cycle(now: datetime) -> None:
    settings = get_settings()
    log = logger.bind(job="scan_cycle", started_at=now.isoformat())
    log.info("job.start")

    async with get_session() as session:
        counts = await run_scan_cycle(session, settings, now=now)
        await session.commit()

    log.info("job.success", **counts)


async def _update_paper_positions(now: datetime) -> None:
    settings = get_settings()
    log = logger.bind(job="paper_positions", started_at=now.isoformat())
    log.info("job.start")

    async with get_session() as session:
        counts = await update_open_positions(session, settings, now)
        await session.commit()

    log.info("job.success", **counts)


async def _fundamental_ingest(now: datetime) -> None:
    settings = get_settings()
    log = logger.bind(job="fundamental_ingest", started_at=now.isoformat())
    log.info("job.start")

    counts: dict[str, int] = {}
    async with get_session() as session:
        counts.update(await ingest_rss.ingest_all(session, now))
        await session.commit()

    # Sesión separada: si Reddit falla catastróficamente, no debe
    # deshacer lo que RSS ya confirmó. Sin credenciales, ingest_reddit
    # devuelve {} sin tocar la red (no es un error).
    async with get_session() as session:
        counts.update(await ingest_reddit.ingest_all(session, settings, now))
        await session.commit()

    log.info("job.success", **counts)


async def fundamental_ingest_job() -> None:
    """Ingesta RSS/JSON + Reddit al almacén PIT (sección 12.2), cada
    `SCAN_INTERVAL_MINUTES` — job independiente de `market_cycle_job`: un
    feed caído no debe afectar al escaneo técnico ni viceversa."""
    started_at = datetime.now(tz=UTC)
    try:
        await _fundamental_ingest(started_at)
    except Exception:
        logger.exception("fundamental_ingest.failed")


async def daily_summary_job() -> None:
    """Resumen diario por Telegram a las 22:00 UTC (sección 17)."""
    settings = get_settings()
    now = datetime.now(tz=UTC)
    try:
        async with get_session() as session:
            text = await build_daily_summary(session, settings, now)
        await send_message(settings, text)
    except Exception:
        logger.exception("daily_summary.failed")


async def market_cycle_job() -> None:
    """Ingesta velas 1h/4h y ticker 24h para todo el universo (idempotente,
    un fallo a mitad de ciclo se corrige solo en el siguiente run), corre el
    scanner+técnico+risk engine (`trigger=scheduled`) sobre datos ya frescos
    y, por último, sigue las posiciones de papel abiertas (sección 11,
    simplificado a esta misma cadencia — ver `services/execution/
    paper_ledger.py`)."""
    started_at = datetime.now(tz=UTC)
    log = logger.bind(job="market_cycle", started_at=started_at.isoformat())

    try:
        await _ingest_market_data(started_at)
    except Exception:
        # Fail-closed: si la ingesta falla, no tiene sentido escanear con
        # datos potencialmente obsoletos; se reintenta todo en el siguiente
        # disparo del scheduler.
        log.exception("ingest.failed")
        return

    try:
        await _scan_cycle(started_at)
    except Exception:
        log.exception("scan.failed")
        return

    try:
        await _update_paper_positions(started_at)
    except Exception:
        log.exception("paper_positions.failed")
        return

    duration_s = (datetime.now(tz=UTC) - started_at).total_seconds()
    log.info("cycle.complete", duration_s=duration_s)


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        market_cycle_job,
        trigger="interval",
        minutes=settings.scan_interval_minutes,
        id="market_cycle",
        next_run_time=datetime.now(tz=UTC),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        fundamental_ingest_job,
        trigger="interval",
        minutes=settings.scan_interval_minutes,
        id="fundamental_ingest",
        next_run_time=datetime.now(tz=UTC),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_summary_job,
        trigger="cron",
        hour=22,
        minute=0,
        timezone=UTC,
        id="daily_summary",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("scheduler.started", interval_minutes=settings.scan_interval_minutes)
    return scheduler
