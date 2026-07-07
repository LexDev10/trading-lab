"""Datos de solo lectura para el dashboard mínimo (sección 4/19, fase 3)
y para `scripts/estado.py` — una sola fuente de verdad para cada
pregunta, nunca dos cálculos del mismo número (regla crítica, sección 6).

Todo aquí lee tablas ya existentes (`equity_snapshots`, `trade_entries`/
`trade_exits`, `decision_logs`, `item_classifications`, `system_state`);
no escribe nada."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from db.models import DecisionLog, EquitySnapshot, ItemClassification, SystemState, TradeEntry, TradeExit
from services.fundamental.veto import asset_has_active_veto
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
class ClosedTradeRow:
    asset: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    exit_type: str
    pnl_quote: Decimal
    pnl_pct_net: Decimal


async def get_closed_trades_history(session: AsyncSession, limit: int = 50) -> list[ClosedTradeRow]:
    """Historial fila-a-fila (no el agregado de `compute_closed_trades_summary`)
    para la tabla de trades del dashboard."""
    stmt = (
        select(
            TradeEntry.asset, TradeEntry.entry_time, TradeEntry.entry_price,
            TradeExit.exit_time, TradeExit.exit_price, TradeExit.exit_type,
            TradeExit.pnl_quote, TradeExit.pnl_pct_net,
        )
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .where(TradeEntry.environment == ENVIRONMENT)
        .order_by(TradeExit.exit_time.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        ClosedTradeRow(
            asset=asset, entry_time=entry_time, exit_time=exit_time, entry_price=entry_price,
            exit_price=exit_price, exit_type=exit_type, pnl_quote=pnl_quote, pnl_pct_net=pnl_pct_net,
        )
        for asset, entry_time, entry_price, exit_time, exit_price, exit_type, pnl_quote, pnl_pct_net in result.all()
    ]


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
    id: int
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
            id=row.id, ts=row.ts, asset=row.asset, mode=row.mode, final_action=row.final_action,
            rejection_reasons=row.rejection_reasons,
        )
        for row in result.scalars().all()
    ]


@dataclass
class DecisionDetail:
    """Igual que `RecentDecision` pero con el JSONB del risk_verdict —
    el dashboard lo usa para pintar el checklist completo verde/rojo de
    una decisión concreta (sección 4/19), sin reinterpretar el veredicto:
    se muestra tal cual lo dejó `services/risk/engine.py`."""

    ts: datetime
    asset: str
    mode: str
    trigger: str
    final_action: str
    rejection_reasons: list[str]
    technical: dict[str, Any] | None
    risk_verdict: dict[str, Any] | None
    decision: dict[str, Any] | None


async def get_decision_detail(session: AsyncSession, decision_log_id: int) -> DecisionDetail | None:
    stmt = select(DecisionLog).where(DecisionLog.id == decision_log_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return DecisionDetail(
        ts=row.ts, asset=row.asset, mode=row.mode, trigger=row.trigger, final_action=row.final_action,
        rejection_reasons=row.rejection_reasons, technical=row.technical_jsonb,
        risk_verdict=row.risk_verdict_jsonb, decision=row.decision_jsonb,
    )


@dataclass
class OpenPositionDetail:
    """Posiciones de PAPEL pendientes/abiertas (sección 11, fase 1) —
    `status='pending'` significa orden límite simulada esperando que el
    precio toque `entry_zone` dentro de `entry_ttl_minutes` (bug #11,
    `evaluate_pending_fill`); `status='open'` ya tiene fill real."""

    id: int
    asset: str
    status: str
    timeframe: str | None
    entry_time: datetime
    entry_price: Decimal
    entry_zone_low: Decimal | None
    entry_zone_high: Decimal | None
    qty: Decimal
    sl: Decimal
    tp: Decimal


async def get_open_positions_detail(session: AsyncSession) -> list[OpenPositionDetail]:
    stmt = (
        select(TradeEntry)
        .where(TradeEntry.environment == ENVIRONMENT, TradeEntry.status.in_(("pending", "open")))
        .order_by(TradeEntry.entry_time.desc())
    )
    result = await session.execute(stmt)
    return [
        OpenPositionDetail(
            id=row.id, asset=row.asset, status=row.status, timeframe=row.timeframe, entry_time=row.entry_time,
            entry_price=row.entry_price, entry_zone_low=row.entry_zone_low, entry_zone_high=row.entry_zone_high,
            qty=row.qty, sl=row.sl, tp=row.tp,
        )
        for row in result.scalars().all()
    ]


@dataclass
class SystemStateInfo:
    state: str
    halted_at: datetime | None
    halted_reason: str | None


async def get_system_state_info(session: AsyncSession) -> SystemStateInfo:
    """Mismo criterio de lectura que `services/risk/portfolio_state.py::
    _get_system_state` (sin fila -> `running`) — se reimplementa aquí en
    vez de importar esa función privada porque es una consulta trivial de
    una sola fila; si la semántica de `system_state` cambia, actualizar
    ambas lecturas."""
    stmt = select(SystemState).where(SystemState.id == 1)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return SystemStateInfo(state="running", halted_at=None, halted_reason=None)
    return SystemStateInfo(state=row.state, halted_at=row.halted_at, halted_reason=row.halted_reason)


async def get_active_vetoes(session: AsyncSession, settings: Settings, now: datetime) -> list[str]:
    """Activos del universo con veto fundamental activo AHORA (bloquea
    entradas nuevas, sección 12.4) — reutiliza `asset_has_active_veto`
    (única fuente de verdad, misma función que usa el scanner real), no
    una segunda implementación del criterio de ventana/fuentes."""
    active: list[str] = []
    for symbol in settings.universe_list:
        base = symbol.removesuffix("USDT")
        if await asset_has_active_veto(session, base, settings, now):
            active.append(base)
    return active


@dataclass
class RecentClassification:
    classified_at: datetime
    published_at: datetime | None
    item_kind: str
    source: str | None
    stance: str
    veto: bool
    asset_tags: list[str]
    summary: str


async def get_recent_classifications(session: AsyncSession, limit: int = 30) -> list[RecentClassification]:
    stmt = select(ItemClassification).order_by(ItemClassification.classified_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [
        RecentClassification(
            classified_at=row.classified_at, published_at=row.published_at, item_kind=row.item_kind,
            source=row.source, stance=row.stance, veto=row.veto, asset_tags=row.asset_tags, summary=row.summary,
        )
        for row in result.scalars().all()
    ]
