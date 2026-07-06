"""Equivalente CLI de `/estado` — sección 21 ("posiciones abiertas, equity,
régimen BTC, modo del sistema, drawdown actual"). Hoy specado solo para
Telegram (bloqueado por credenciales); este comando cubre lo mismo vía CLI,
sin depender de Telegram, y añade el resumen de rentabilidad del paper
ledger (`services/execution/paper_ledger.py`) que es lo que permite ver el
rendimiento real de las señales sin necesitar credenciales de exchange.

Uso (con el stack levantado):
    docker compose exec app uv run python -m scripts.estado
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from db.models import EquitySnapshot, RegimeLog, SystemState, TradeEntry
from db.session import get_session
from services.reporting.dashboard_data import compute_closed_trades_summary
from services.risk.portfolio_state import ENVIRONMENT


async def _print_system_state(session) -> None:
    row = await session.scalar(select(SystemState).where(SystemState.id == 1))
    state = row.state if row else "running"
    print(f"Sistema: {state}" + (f"  (motivo: {row.halted_reason})" if row and row.state == "halt" else ""))


async def _print_regime(session) -> None:
    row = await session.scalar(select(RegimeLog).order_by(RegimeLog.ts.desc()).limit(1))
    if row is None:
        print("Régimen BTC: sin datos todavía")
        return
    print(f"Régimen BTC (4h, {row.ts.isoformat()}): {row.btc_regime}")


async def _print_equity(session) -> None:
    row = await session.scalar(
        select(EquitySnapshot)
        .where(EquitySnapshot.environment == ENVIRONMENT)
        # Por id (orden de inserción), igual que portfolio_state.get_latest_equity
        # — ver FIX 2026-07-06 en ese módulo.
        .order_by(EquitySnapshot.id.desc())
        .limit(1)
    )
    if row is None:
        print("Equity (paper): sin operaciones cerradas todavía (arranque configurado en PAPER_STARTING_EQUITY_USDT)")
        return
    print(f"Equity (paper): {row.equity_quote} USDT  |  drawdown: {row.drawdown_pct:.2%}  ({row.ts.isoformat()})")


async def _print_open_positions(session, now: datetime) -> None:
    result = await session.execute(
        select(TradeEntry).where(TradeEntry.environment == ENVIRONMENT, TradeEntry.status == "open")
    )
    entries = list(result.scalars().all())
    print(f"\nPosiciones de papel abiertas: {len(entries)}")
    for entry in entries:
        age = now - entry.entry_time
        print(
            f"  - {entry.asset}: entry={entry.entry_price} sl={entry.sl} tp={entry.tp} "
            f"qty={entry.qty} antigüedad={age}"
        )


async def _print_closed_summary(session) -> None:
    summary = await compute_closed_trades_summary(session)
    print(f"\nOperaciones de papel cerradas: {summary.n_trades}")
    if summary.n_trades == 0:
        return

    print(f"  win_rate: {summary.win_rate:.1%}")
    print(f"  pnl total: {summary.total_pnl_quote:.4f} USDT")
    print(f"  pnl% medio por operación: {summary.avg_pnl_pct:.2%}")
    print(
        f"  profit_factor: {summary.profit_factor:.2f}"
        if summary.profit_factor is not None
        else "  profit_factor: n/a (sin pérdidas)"
    )


async def estado() -> None:
    now = datetime.now(tz=UTC)
    print("=" * 60)
    print("  /estado")
    print("=" * 60)
    async with get_session() as session:
        await _print_system_state(session)
        await _print_regime(session)
        await _print_equity(session)
        await _print_open_positions(session, now)
        await _print_closed_summary(session)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(estado())
