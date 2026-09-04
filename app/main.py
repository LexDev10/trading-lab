"""FastAPI: /health y /dashboard (sección 4/19, fase 3)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import get_settings
from app.dashboard import render_dashboard
from app.scheduler import start_scheduler
from core.git_info import get_git_sha
from core.logging import configure_logging, get_logger
from db.models import SystemState
from db.session import get_session
from services.reporting.dashboard_data import STALE_AFTER_HOURS, get_latest_candle_time

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    scheduler = start_scheduler()
    logger.info("app.startup", git_sha=get_git_sha())
    yield
    scheduler.shutdown(wait=False)
    logger.info("app.shutdown")


app = FastAPI(title="trading-lab", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    settings = get_settings()
    db_ok = True
    latest_candle_at = None
    system_state = "running"
    try:
        async with get_session() as session:
            latest_candle_at = await get_latest_candle_time(session)
            state_row = await session.execute(select(SystemState.state).where(SystemState.id == 1))
            state_value = state_row.scalar_one_or_none()
            if state_value is not None:
                system_state = state_value
    except Exception:
        db_ok = False

    stale = True
    if latest_candle_at is not None:
        age_hours = (datetime.now(tz=UTC) - latest_candle_at).total_seconds() / 3600
        stale = age_hours > STALE_AFTER_HOURS  # 2x timeframe de 1h, ver sección 8.2

    return {
        "status": "ok" if db_ok else "degraded",
        "db_ok": db_ok,
        "data_fresh": not stale,
        "latest_candle_at": latest_candle_at,
        "mode": settings.mode,
        "environment": settings.environment,
        "system_state": system_state,
        "git_sha": get_git_sha(),
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    async with get_session() as session:
        return await render_dashboard(session)
