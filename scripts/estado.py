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
from decimal import Decimal

from sqlalchemy import select

from db.models import EquitySnapshot, RegimeLog, SystemState, TradeEntry, TradeExit
from db.session import get_session

ENVIRONMENT = "paper"


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
        .order_by(EquitySnapshot.ts.desc())
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
    result = await session.execute(
        select(TradeExit.pnl_quote, TradeExit.pnl_pct_net)
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .where(TradeEntry.environment == ENVIRONMENT)
    )
    rows = result.all()
    print(f"\nOperaciones de papel cerradas: {len(rows)}")
    if not rows:
        return

    pnl_quotes = [r.pnl_quote for r in rows]
    pnl_pcts = [r.pnl_pct_net for r in rows]
    wins = [p for p in pnl_quotes if p > 0]
    losses = [p for p in pnl_quotes if p <= 0]
    win_rate = Decimal(len(wins)) / Decimal(len(rows))
    total_pnl = sum(pnl_quotes, Decimal("0"))
    avg_pnl_pct = sum(pnl_pcts, Decimal("0")) / Decimal(len(pnl_pcts))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    print(f"  win_rate: {win_rate:.1%}")
    print(f"  pnl total: {total_pnl:.4f} USDT")
    print(f"  pnl% medio por operación: {avg_pnl_pct:.2%}")
    print(f"  profit_factor: {profit_factor:.2f}" if profit_factor is not None else "  profit_factor: n/a (sin pérdidas)")


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
