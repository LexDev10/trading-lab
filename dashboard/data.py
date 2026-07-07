"""Puente síncrono entre Streamlit (ejecuta el script completo de nuevo
en cada interacción, siempre síncrono) y las lecturas async de
`services/reporting/dashboard_data.py` — MISMAS consultas, ninguna
reimplementación (regla crítica, sección 6 del spec).

# DECISION: cada llamada crea y libera su PROPIO engine/sesión dentro de
# una única ejecución de `asyncio.run()`, en vez de reutilizar el engine
# compartido de `db/session.py`. Ese engine es un singleton de módulo
# cuyo pool de asyncpg queda atado al primer event loop que lo usa,
# pero `asyncio.run()` crea un loop nuevo en CADA llamada — reutilizarlo
# entre llamadas revienta con "Future attached to a different loop"
# (el mismo problema que `pyproject.toml` ya documenta haber evitado en
# los tests con `asyncio_default_fixture_loop_scope=session`). Crear un
# engine efímero por rerun es algo más caro, pero un dashboard de un solo
# usuario con recargas cada pocos segundos no lo nota, y es la forma
# correcta de no compartir un loop entre ejecuciones aisladas.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from scripts.analiza import ManualAnalysisResult
from services.reporting import dashboard_data as _dd

T = TypeVar("T")


@asynccontextmanager
async def _fresh_session():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


def _run(fn: Callable[..., Awaitable[T]], *args: object, **kwargs: object) -> T:
    async def _wrapped() -> T:
        async with _fresh_session() as session:
            return await fn(session, *args, **kwargs)

    return asyncio.run(_wrapped())


def count_open_positions() -> int:
    return _run(_dd.count_open_positions)


def get_equity_curve(limit: int = 500) -> list[_dd.EquityPoint]:
    return _run(_dd.get_equity_curve, limit=limit)


def compute_closed_trades_summary() -> _dd.ClosedTradesSummary:
    return _run(_dd.compute_closed_trades_summary)


def get_closed_trades_history(limit: int = 50) -> list[_dd.ClosedTradeRow]:
    return _run(_dd.get_closed_trades_history, limit=limit)


def get_decisions_by_mode() -> list[_dd.ModeActionCount]:
    return _run(_dd.get_decisions_by_mode)


def get_recent_decisions(limit: int = 30) -> list[_dd.RecentDecision]:
    return _run(_dd.get_recent_decisions, limit=limit)


def get_decision_detail(decision_log_id: int) -> _dd.DecisionDetail | None:
    return _run(_dd.get_decision_detail, decision_log_id)


def get_open_positions_detail() -> list[_dd.OpenPositionDetail]:
    return _run(_dd.get_open_positions_detail)


def get_system_state_info() -> _dd.SystemStateInfo:
    return _run(_dd.get_system_state_info)


def get_recent_classifications(limit: int = 30) -> list[_dd.RecentClassification]:
    return _run(_dd.get_recent_classifications, limit=limit)


def get_active_vetoes() -> list[str]:
    settings = get_settings()
    return _run(_dd.get_active_vetoes, settings, datetime.now(tz=UTC))


def get_universe() -> list[str]:
    return get_settings().universe_list


# ─────────────────────────────────────────────────────────────────────
# Análisis manual ("Analizar ahora") — botón de la fase 3.
#
# DECISION: a diferencia de las lecturas de arriba, `run_manual_analysis`
# (scripts/analiza.py) ESCRIBE en la base de datos (decision_log,
# posiciones de papel) usando el engine COMPARTIDO de `db/session.py` —
# el mismo que usan la app real y el scheduler. No queremos un segundo
# pool de escritura en paralelo solo para el dashboard, así que aquí NO
# se puede usar el patrón "engine efímero por `asyncio.run()`" de arriba:
# si cada clic abriera y cerrara su propio loop, la conexión que el
# engine compartido deja en el pool tras el primer clic quedaría atada a
# un loop ya cerrado, y el SEGUNDO clic reventaría con "Future attached
# to a different loop" (el mismo problema que el pyproject.toml ya
# documenta para los tests). La solución correcta aquí es un ÚNICO event
# loop de fondo, vivo durante toda la vida del proceso del dashboard, al
# que cada clic programa su corrutina vía `run_coroutine_threadsafe` —
# así el engine compartido siempre ve el MISMO loop, click tras click.
# ─────────────────────────────────────────────────────────────────────

_background_loop: asyncio.AbstractEventLoop | None = None
_background_loop_lock = threading.Lock()


def _get_background_loop() -> asyncio.AbstractEventLoop:
    global _background_loop
    with _background_loop_lock:
        if _background_loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, daemon=True, name="dashboard-manual-analysis-loop").start()
            _background_loop = loop
        return _background_loop


def run_manual_analysis(asset: str, operar: bool = False) -> ManualAnalysisResult:
    from scripts.analiza import run_manual_analysis as _run_manual_analysis

    loop = _get_background_loop()
    future = asyncio.run_coroutine_threadsafe(_run_manual_analysis(asset, operar), loop)
    return future.result()
