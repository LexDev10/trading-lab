"""FastAPI: /health (dashboard llega en fase 3)."""

import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import select

from app.config import get_settings
from app.scheduler import start_scheduler
from core.logging import configure_logging, get_logger
from db.models import Candle
from db.session import get_session

logger = get_logger("app")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    scheduler = start_scheduler()
    logger.info("app.startup", git_sha=_git_sha())
    yield
    scheduler.shutdown(wait=False)
    logger.info("app.shutdown")


app = FastAPI(title="trading-lab", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    settings = get_settings()
    db_ok = True
    latest_candle_at = None
    try:
        async with get_session() as session:
            result = await session.execute(select(Candle.open_time).order_by(Candle.open_time.desc()).limit(1))
            latest_candle_at = result.scalar_one_or_none()
    except Exception:
        db_ok = False

    stale = True
    if latest_candle_at is not None:
        age_hours = (datetime.now(tz=UTC) - latest_candle_at).total_seconds() / 3600
        stale = age_hours > 2  # 2x timeframe de 1h, ver sección 8.2

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "data_fresh": not stale,
        "latest_candle_at": latest_candle_at,
        "mode": settings.mode,
        "environment": settings.environment,
        "system_state": "running",  # halt/killswitch llega con el risk engine (fase 1)
        "git_sha": _git_sha(),
    }
