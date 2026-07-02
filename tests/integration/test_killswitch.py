"""Integration test contra Postgres real (sección 18, killswitch "probado"):
el halt manual (`scripts.halt`) bloquea nuevas entradas en el risk engine
(check `system_not_halted`) y el rearme (`scripts.rearm`) las desbloquea.
Nunca automático — sección 9.2: "requiere rearme manual por CLI/endpoint".

Requiere una base de datos Postgres real y alcanzable (DATABASE_URL). No
se ejecuta en el ciclo rápido de tests unitarios sin DB; correr con:

    docker compose exec app uv run pytest tests/integration -v
"""

from datetime import UTC, datetime
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


async def _seed_equity(equity: Decimal) -> None:
    async with get_session() as session:
        session.add(
            EquitySnapshot(
                ts=datetime.now(tz=UTC),
                environment="test",
                equity_quote=equity,
                open_positions=0,
                drawdown_pct=Decimal("0"),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_halt_blocks_entries_and_rearm_unblocks_them():
    settings = get_settings()
    await _seed_equity(Decimal("10000"))

    # 1. Sistema running (default, sin fila en system_state) -> aprobado.
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    verdict = evaluate_risk(_valid_risk_input(), portfolio, settings)
    assert verdict.checks["system_not_halted"] is True
    assert verdict.approved is True

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

    # 4. Rearme manual explícito -> vuelve a aprobar.
    await rearm()
    async with get_session() as session:
        portfolio = await build_portfolio_snapshot(session, settings, "SOLUSDT")
    verdict = evaluate_risk(_valid_risk_input(), portfolio, settings)
    assert verdict.checks["system_not_halted"] is True
    assert verdict.approved is True
