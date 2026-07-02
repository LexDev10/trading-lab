"""APScheduler in-process. Un único job de ingesta cada SCAN_INTERVAL_MINUTES.
Sin Celery, sin colas externas — swing corto no las necesita (sección 5)."""

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from core.logging import get_logger
from db.session import get_session
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import insert_market_snapshots, upsert_asset, upsert_candles

logger = get_logger("scheduler")

TIMEFRAMES = ("1h", "4h")


async def ingest_market_data_job() -> None:
    """Ingesta velas 1h/4h y ticker 24h para todo el universo. Idempotente
    (upsert), así que un fallo a mitad de ciclo se corrige solo en el
    siguiente run."""
    settings = get_settings()
    client = BinanceMarketData()
    started_at = datetime.now(tz=UTC)
    log = logger.bind(job="ingest_market_data", started_at=started_at.isoformat())
    log.info("job.start")

    try:
        async with get_session() as session:
            for asset in settings.universe_list:
                await upsert_asset(session, asset)
                for timeframe in TIMEFRAMES:
                    candles = await asyncio.to_thread(client.fetch_klines, asset, timeframe, 500)
                    await upsert_candles(session, candles)

            snapshots = await asyncio.to_thread(client.fetch_ticker_24h, settings.universe_list)
            await insert_market_snapshots(session, snapshots)
            await session.commit()

        duration_s = (datetime.now(tz=UTC) - started_at).total_seconds()
        log.info("job.success", duration_s=duration_s, assets=len(settings.universe_list))
    except Exception:
        # Fail-closed: se loguea el error y el ciclo se reintenta en el
        # siguiente disparo del scheduler; no se propaga para no tumbar el
        # scheduler entero por un fallo transitorio de red/API.
        log.exception("job.failed")


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        ingest_market_data_job,
        trigger="interval",
        minutes=settings.scan_interval_minutes,
        id="ingest_market_data",
        next_run_time=datetime.now(tz=UTC),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("scheduler.started", interval_minutes=settings.scan_interval_minutes)
    return scheduler
