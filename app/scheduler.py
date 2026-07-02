"""APScheduler in-process. Un único job cada SCAN_INTERVAL_MINUTES que
encadena ingesta de mercado + ciclo de scanner (sección 5: sin Celery, sin
colas externas — swing corto no las necesita)."""

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from core.logging import get_logger
from db.session import get_session
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import insert_market_snapshots, upsert_asset, upsert_candles
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


async def market_cycle_job() -> None:
    """Ingesta velas 1h/4h y ticker 24h para todo el universo (idempotente,
    un fallo a mitad de ciclo se corrige solo en el siguiente run) y, a
    continuación, corre el scanner+técnico+risk engine (`trigger=scheduled`)
    sobre datos ya frescos."""
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
    scheduler.start()
    logger.info("scheduler.started", interval_minutes=settings.scan_interval_minutes)
    return scheduler
