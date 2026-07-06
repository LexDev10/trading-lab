"""Integration test contra Postgres real (sección 18, killswitch "probado"):
el halt manual (`scripts.halt`) bloquea nuevas entradas en el risk engine
(check `system_not_halted`) y el rearme (`scripts.rearm`) las desbloquea.
Nunca automático — sección 9.2: "requiere rearme manual por CLI/endpoint".

Requiere una base de datos Postgres real y alcanzable (DATABASE_URL). No
se ejecuta en el ciclo rápido de tests unitarios sin DB; correr con:

    docker compose exec app uv run pytest tests/integration -v
"""

from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.config import get_settings
from db.models import EquitySnapshot, SystemState
from db.session import get_session
from services.risk.engine import RiskInput, evaluate_risk
from services.risk.portfolio_state import build_portfolio_snapshot
from scripts.halt import halt
from scripts.rearm import rearm

pytestmark = pytest.mark.integration


def _valid_risk_input() -> RiskInput:
    return RiskInput(
        asset="SOLUSDT",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("115"),
        atr_14=Decimal("4"),
        spread_bps=Decimal("3"),
        min_notional=Decimal("10"),
        regime_blocks_entries=False,
    )


@pytest.fixture(autouse=True)
async def _clean_system_state():
    async with get_session() as session:
        await session.execute(delete(SystemState))
        await session.execute(delete(EquitySnapshot).where(EquitySnapshot.environment == "test"))
        await session.commit()
    yield
    async with get_session() as session:
        await session.execute(delete(SystemState))
        await session.execute(delete(EquitySnapshot).where(EquitySnapshot.environment == "test"))
        await session.commit()


@pytest.mark.asyncio
async def test_halt_blocks_entries_and_rearm_unblocks_them():
    """Este test comparte Postgres con la app real corriendo en paralelo
    (scheduler en vivo) — puede haber posiciones de papel reales abiertas
    afectando exposición/correlación. Por eso solo se afirma sobre
    `checks["system_not_halted"]` (lo único que este test ejercita) en las
    direcciones donde el resultado es determinista pase lo que pase con el
    resto de checks:
    - Tras `halt`, `approved` SIEMPRE es `False` (un check en falso basta).
    - Sin halt, `system_not_halted` es `True`, pero `approved` global
      depende de checks que si hay trading real concurrente, no lo son
      (exposición, correlación) — no se afirma sobre `approved` ahí."""
    settings = get_settings()
    # Nota: antes se sembraba un EquitySnapshot con environment='test' —
    # desde el FIX 2026-07-06 (portfolio_state filtra por environment) esas
    # filas ya no influyen en el risk engine, así que se eliminó el seed;
    # la equity cae al bootstrap PAPER_STARTING_EQUITY_USDT (irrelevante
    # para lo que este test afirma).

    # 1. Sistema running (default, sin fila en system_state) -> el check
    #    específico de halt está en verde.
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    verdict = evaluate_risk(_valid_risk_input(), portfolio, settings)
    assert verdict.checks["system_not_halted"] is True

    # 2. Halt manual -> bloquea, aunque el resto de checks siga en verde.
    await halt("prueba de integración: drawdown simulado")
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    verdict = evaluate_risk(_valid_risk_input(), portfolio, settings)
    assert verdict.checks["system_not_halted"] is False
    assert verdict.approved is False
    assert portfolio.system_halted_reason == "prueba de integración: drawdown simulado"

    # 3. Sin rearme, sigue bloqueado (no hay auto-recuperación).
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    assert portfolio.system_state == "halt"

    # 4. Rearme manual explícito -> el check de halt vuelve a verde.
    await rearm()
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    verdict = evaluate_risk(_valid_risk_input(), portfolio, settings)
    assert verdict.checks["system_not_halted"] is True
