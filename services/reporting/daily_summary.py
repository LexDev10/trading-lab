"""Resumen diario para Telegram (sección 17: "resumen diario 22:00 UTC
-equity, trades del día, setups rechazados por motivo") + vigilancia core
de `CORE_ASSETS` (sección 21.4: régimen/ATR%/EMA aunque no haya setup).

Reutiliza piezas ya existentes en vez de recalcular nada: `portfolio_state`
para equity/drawdown, y `compute_btc_regime` (agnóstica al activo pese al
nombre) para el estado de cada `CORE_ASSET` — aquí solo se usa para
mostrar, no escribe en `regime_log` (esa tabla es específicamente el
régimen de BTC que consume el risk engine, sección 8.3)."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from db.models import DecisionLog, TradeEntry, TradeExit
from services.data.persistence import get_recent_candles
from services.risk.portfolio_state import ENVIRONMENT, build_portfolio_snapshot
from services.scanner.regime import compute_btc_regime
from services.technical.indicators import candles_to_frame


async def _trades_today(session: AsyncSession, day_start: datetime) -> tuple[int, Decimal]:
    stmt = (
        select(TradeExit.pnl_quote)
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .where(TradeEntry.environment == ENVIRONMENT, TradeExit.exit_time >= day_start)
    )
    result = await session.execute(stmt)
    pnl_quotes = [row[0] for row in result.all()]
    return len(pnl_quotes), sum(pnl_quotes, Decimal("0"))


async def _rejections_today(session: AsyncSession, day_start: datetime) -> list[tuple[str, int]]:
    reason_col = func.unnest(DecisionLog.rejection_reasons).label("reason")
    stmt = (
        select(reason_col, func.count())
        .where(DecisionLog.ts >= day_start)
        .group_by(reason_col)
        .order_by(func.count().desc())
    )
    result = await session.execute(stmt)
    return [(reason, count) for reason, count in result.all()]


async def _core_assets_status(session: AsyncSession, settings: Settings, now: datetime) -> list[str]:
    lines = []
    for asset in settings.core_assets_list:
        candles = await get_recent_candles(session, asset, "4h", 500)
        if not candles:
            lines.append(f"  {asset}: sin datos todavía")
            continue
        df = candles_to_frame(candles, now=now)
        regime, details = compute_btc_regime(df)
        if "reason" in details:
            lines.append(f"  {asset}: {regime.value} ({details['reason']}, {details['candles']} velas)")
            continue
        lines.append(
            f"  {asset}: {regime.value}  price={details['price']:.4f}  "
            f"ema50={details['ema50']:.4f}  ema200={details['ema200']:.4f}  "
            f"atr%={details['atr_pct']:.2f}"
        )
    return lines


async def build_daily_summary(session: AsyncSession, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    portfolio = await build_portfolio_snapshot(session, settings, settings.core_assets_list[0], now)
    trades_count, trades_pnl = await _trades_today(session, day_start)
    rejections = await _rejections_today(session, day_start)
    core_lines = await _core_assets_status(session, settings, now)

    lines = [
        f"📊 <b>Resumen diario</b> {now.date().isoformat()}",
        f"Sistema: {portfolio.system_state}",
        f"Equity: {portfolio.equity_quote:.2f} USDT  drawdown={portfolio.drawdown_pct:.2%}  "
        f"posiciones abiertas={portfolio.open_positions}",
        f"Trades cerrados hoy: {trades_count}  pnl total={trades_pnl:.4f} USDT",
        "",
        "Rechazos por motivo (hoy):",
    ]
    if rejections:
        lines.extend(f"  {reason}: {count}" for reason, count in rejections)
    else:
        lines.append("  (ninguno)")

    lines.append("")
    lines.append("Vigilancia core:")
    lines.extend(core_lines)

    return "\n".join(lines)
