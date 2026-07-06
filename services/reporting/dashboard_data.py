"""Datos de solo lectura para el dashboard mínimo (sección 4/19, fase 3)
y para `scripts/estado.py` — una sola fuente de verdad para cada
pregunta, nunca dos cálculos del mismo número (regla crítica, sección 6).

Todo aquí lee tablas ya existentes (`equity_snapshots`, `trade_entries`/
`trade_exits`, `decision_logs`); no escribe nada."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DecisionLog, EquitySnapshot, TradeEntry, TradeExit
from services.risk.portfolio_state import ENVIRONMENT


async def count_open_positions(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(TradeEntry).where(
        TradeEntry.environment == ENVIRONMENT, TradeEntry.status == "open"
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


@dataclass
class EquityPoint:
    ts: datetime
    equity_quote: Decimal
    drawdown_pct: Decimal


async def get_equity_curve(session: AsyncSession, limit: int = 500) -> list[EquityPoint]:
    """Ascendente por `id` (orden de inserción — mismo criterio que el
    fix del bug #1 en `services/risk/portfolio_state.py::get_latest_equity`,
    nunca por `ts`)."""
    stmt = (
        select(EquitySnapshot)
        .where(EquitySnapshot.environment == ENVIRONMENT)
        .order_by(EquitySnapshot.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(reversed(result.scalars().all()))
    return [EquityPoint(ts=r.ts, equity_quote=r.equity_quote, drawdown_pct=r.drawdown_pct) for r in rows]


@dataclass
class ClosedTradesSummary:
    n_trades: int
    win_rate: Decimal | None
    total_pnl_quote: Decimal
    avg_pnl_pct: Decimal | None
    profit_factor: Decimal | None


async def compute_closed_trades_summary(session: AsyncSession) -> ClosedTradesSummary:
    """Extraído de `scripts/estado.py` (antes calculaba esto solo para
    imprimir) para que el dashboard use la MISMA fórmula, no una
    reimplementación."""
    stmt = (
        select(TradeExit.pnl_quote, TradeExit.pnl_pct_net)
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .where(TradeEntry.environment == ENVIRONMENT)
    )
    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return ClosedTradesSummary(
            n_trades=0, win_rate=None, total_pnl_quote=Decimal("0"), avg_pnl_pct=None, profit_factor=None
        )

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

    return ClosedTradesSummary(
        n_trades=len(rows),
        win_rate=win_rate,
        total_pnl_quote=total_pnl,
        avg_pnl_pct=avg_pnl_pct,
        profit_factor=profit_factor,
    )


@dataclass
class ModeActionCount:
    mode: str
    final_action: str
    count: int


async def get_decisions_by_mode(session: AsyncSession) -> list[ModeActionCount]:
    """Ablación "solo-modo-activo" (sección 13, decisión de esta ronda):
    agrupa histórico por `decision_logs.mode` — cada decisión ya guarda
    bajo qué modo se tomó, así que comparar modos es comparar periodos
    en los que `MODE` estuvo configurado distinto, no una simulación en
    paralelo."""
    stmt = (
        select(DecisionLog.mode, DecisionLog.final_action, func.count())
        .group_by(DecisionLog.mode, DecisionLog.final_action)
        .order_by(DecisionLog.mode, DecisionLog.final_action)
    )
    result = await session.execute(stmt)
    return [ModeActionCount(mode=mode, final_action=action, count=count) for mode, action, count in result.all()]


@dataclass
class RecentDecision:
    ts: datetime
    asset: str
    mode: str
    final_action: str
    rejection_reasons: list[str]


async def get_recent_decisions(session: AsyncSession, limit: int = 30) -> list[RecentDecision]:
    stmt = select(DecisionLog).order_by(DecisionLog.ts.desc()).limit(limit)
    result = await session.execute(stmt)
    return [
        RecentDecision(
            ts=row.ts, asset=row.asset, mode=row.mode, final_action=row.final_action,
            rejection_reasons=row.rejection_reasons,
        )
        for row in result.scalars().all()
    ]
