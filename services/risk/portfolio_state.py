"""Estado de cartera para los checks de la sección 9.2. Se construye leyendo
`trade_entries`/`trade_exits`/`equity_snapshots`/`system_state` — nunca se
recalcula "a mano" en el risk engine, para que ejecución y backtesting/paper
compartan la misma fuente de verdad."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from db.models import DecisionLog, EquitySnapshot, SystemState, TradeEntry, TradeExit


@dataclass
class PortfolioSnapshot:
    equity_quote: Decimal
    open_positions: int
    exposure_total_quote: Decimal
    exposure_btc_beta_quote: Decimal  # MVP: todos los majors son un único bucket (sección 9.2)
    daily_realized_pnl_pct: Decimal
    drawdown_pct: Decimal
    system_state: str
    system_halted_reason: str | None
    asset_cooldown_active: bool
    losses_cooldown_active: bool
    raw: dict[str, Any]


async def get_latest_equity(session: AsyncSession, settings: Settings) -> Decimal:
    """Última equity conocida (o el bootstrap `PAPER_STARTING_EQUITY_USDT`
    si todavía no hay ningún `equity_snapshot`). Pública porque también la
    usa `services/execution/paper_ledger.py` al cerrar una posición —
    misma fuente de verdad que el risk engine, sin duplicar la query."""
    stmt = select(EquitySnapshot.equity_quote).order_by(EquitySnapshot.ts.desc()).limit(1)
    result = await session.execute(stmt)
    equity = result.scalar_one_or_none()
    if equity is not None:
        return equity
    # DECISION: sin histórico de equity_snapshots todavía (no se ha operado
    # ni se ha hecho bootstrap contra la cuenta de testnet real) se usa un
    # valor de arranque configurable, ver Apéndice A / PAPER_STARTING_EQUITY_USDT.
    return settings.paper_starting_equity_usdt


async def _get_open_positions(session: AsyncSession) -> tuple[int, Decimal]:
    stmt = select(TradeEntry.entry_price, TradeEntry.qty).where(TradeEntry.status == "open")
    result = await session.execute(stmt)
    rows = result.all()
    exposure = sum((price * qty for price, qty in rows), Decimal("0"))
    return len(rows), exposure


async def _get_daily_realized_pnl_pct(session: AsyncSession, equity: Decimal, now: datetime) -> Decimal:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(TradeExit.pnl_quote), Decimal("0"))).where(
        TradeExit.exit_time >= day_start
    )
    result = await session.execute(stmt)
    pnl_quote = result.scalar_one()
    if equity <= 0:
        return Decimal("0")
    return pnl_quote / equity


async def _get_equity_peak(session: AsyncSession) -> Decimal | None:
    stmt = select(func.max(EquitySnapshot.equity_quote))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _get_drawdown_pct(session: AsyncSession) -> Decimal:
    peak = await _get_equity_peak(session)
    if peak is None or peak <= 0:
        return Decimal("0")
    current = await session.execute(
        select(EquitySnapshot.equity_quote).order_by(EquitySnapshot.ts.desc()).limit(1)
    )
    current_equity = current.scalar_one_or_none()
    if current_equity is None:
        return Decimal("0")
    return (peak - current_equity) / peak


async def compute_drawdown_pct(session: AsyncSession, current_equity: Decimal) -> Decimal:
    """Drawdown de `current_equity` frente al pico histórico ya persistido en
    `equity_snapshots`. Pensado para quien va a INSERTAR la siguiente fila de
    equity (p.ej. el paper ledger al cerrar una posición) y necesita el mismo
    cálculo que `_get_drawdown_pct` sin duplicarlo ni esperar a que la fila
    nueva exista todavía."""
    peak = await _get_equity_peak(session)
    peak = max(peak, current_equity) if peak is not None else current_equity
    if peak <= 0:
        return Decimal("0")
    return (peak - current_equity) / peak


async def _get_system_state(session: AsyncSession) -> tuple[str, str | None]:
    stmt = select(SystemState).where(SystemState.id == 1)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return "running", None
    return row.state, row.halted_reason


async def _asset_cooldown_active(
    session: AsyncSession, asset: str, cooldown_hours: int, now: datetime
) -> bool:
    stmt = (
        select(TradeExit.exit_time)
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .join(DecisionLog, TradeEntry.decision_log_id == DecisionLog.id)
        .where(DecisionLog.asset == asset)
        .order_by(TradeExit.exit_time.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    last_exit = result.scalar_one_or_none()
    if last_exit is None:
        return False
    return (now - last_exit) < timedelta(hours=cooldown_hours)


async def _losses_cooldown_active(session: AsyncSession, cooldown_hours: int, now: datetime) -> bool:
    stmt = select(TradeExit.exit_type, TradeExit.exit_time).order_by(TradeExit.exit_time.desc()).limit(2)
    result = await session.execute(stmt)
    rows = result.all()
    if len(rows) < 2:
        return False
    last_two_are_sl = all(exit_type == "closed_sl" for exit_type, _ in rows)
    if not last_two_are_sl:
        return False
    most_recent_exit_time: datetime = rows[0][1]
    return bool((now - most_recent_exit_time) < timedelta(hours=cooldown_hours))


async def build_portfolio_snapshot(
    session: AsyncSession, settings: Settings, asset: str, now: datetime | None = None
) -> PortfolioSnapshot:
    now = now or datetime.now(tz=UTC)

    equity = await get_latest_equity(session, settings)
    open_positions, exposure_total = await _get_open_positions(session)
    daily_pnl_pct = await _get_daily_realized_pnl_pct(session, equity, now)
    drawdown_pct = await _get_drawdown_pct(session)
    state, halted_reason = await _get_system_state(session)
    asset_cooldown = await _asset_cooldown_active(session, asset, settings.cooldown_asset_hours, now)
    losses_cooldown = await _losses_cooldown_active(session, settings.cooldown_after_2sl_hours, now)

    return PortfolioSnapshot(
        equity_quote=equity,
        open_positions=open_positions,
        exposure_total_quote=exposure_total,
        exposure_btc_beta_quote=exposure_total,  # bucket único, ver dataclass
        daily_realized_pnl_pct=daily_pnl_pct,
        drawdown_pct=drawdown_pct,
        system_state=state,
        system_halted_reason=halted_reason,
        asset_cooldown_active=asset_cooldown,
        losses_cooldown_active=losses_cooldown,
        raw={
            "equity_quote": str(equity),
            "open_positions": open_positions,
            "exposure_total_quote": str(exposure_total),
            "daily_realized_pnl_pct": str(daily_pnl_pct),
            "drawdown_pct": str(drawdown_pct),
            "system_state": state,
        },
    )
