"""Informe diario para Telegram (sección 17: "resumen diario 22:00 UTC
-equity, trades del día, setups rechazados por motivo") + vigilancia core
de `CORE_ASSETS` (sección 21.4: régimen/ATR%/EMA aunque no haya setup).

Reutiliza piezas ya existentes en vez de recalcular nada (regla crítica,
sección 6): `portfolio_state` para equity/drawdown, `dashboard_data` para
posiciones abiertas, historial de cierres, agregados acumulados, estado
del sistema y vetos activos, y `compute_btc_regime` (agnóstica al activo
pese al nombre) para el estado de cada `CORE_ASSET` — aquí solo se usa
para mostrar, no escribe en `regime_log` (esa tabla es específicamente el
régimen de BTC que consume el risk engine, sección 8.3).

# DECISION (2026-09-04): la VENTANA agregada sigue siendo el día UTC,
# aunque la hora de ENVÍO sea local (`Settings.report_timezone`) — el
# informe y el `daily_loss_limit` deben coincidir en qué cuenta como
# "hoy" (bug #17, CLAUDE.md). La cabecera lo dice explícitamente para
# que no se lea como el día local.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.git_info import get_git_sha
from core.logging import get_logger
from db.models import DecisionLog, TradeEntry, TradeExit
from services.data.persistence import get_recent_candles
from services.reporting.dashboard_data import (
    STALE_AFTER_HOURS,
    compute_closed_trades_summary,
    get_active_vetoes,
    get_closed_trades_history,
    get_latest_candle_time,
    get_open_positions_detail,
    get_system_state_info,
)
from services.risk.portfolio_state import ENVIRONMENT, build_portfolio_snapshot
from services.scanner.regime import compute_btc_regime
from services.technical.indicators import candles_to_frame

logger = get_logger("daily_summary")

MAX_TRADE_ROWS = 15
MAX_POSITION_ROWS = 10


async def _trades_today(session: AsyncSession, day_start: datetime) -> tuple[int, Decimal]:
    # FIX (2026-07-07, bug #17 CODE_REVIEW_2026-07-07.md): "hoy" se cuenta
    # por tiempo de PROCESO (`processed_at`), no por `exit_time` (tiempo de
    # vela) — mismo criterio que `portfolio_state._get_daily_realized_pnl_pct`,
    # para que el resumen diario y el límite de pérdida diaria coincidan
    # en qué cuenta como "hoy".
    stmt = (
        select(TradeExit.pnl_quote)
        .join(TradeEntry, TradeExit.trade_entry_id == TradeEntry.id)
        .where(TradeEntry.environment == ENVIRONMENT, TradeExit.processed_at >= day_start)
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


async def _entries_today(session: AsyncSession, day_start: datetime) -> tuple[int, int]:
    """Órdenes registradas hoy y cuántas expiraron sin fill dentro del TTL
    (`status='expired'`, bug #11) — mide cuánto del embudo se queda en la
    zona de entrada sin llegar a ser posición."""
    stmt = (
        select(TradeEntry.status, func.count())
        .where(TradeEntry.environment == ENVIRONMENT, TradeEntry.entry_time >= day_start)
        .group_by(TradeEntry.status)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    total = sum(count for _, count in rows)
    expired = sum(count for status, count in rows if status == "expired")
    return total, expired


async def _last_closed_price(session: AsyncSession, asset: str, now: datetime) -> Decimal | None:
    """Último cierre 1h YA CERRADO. La ingesta persiste también la kline en
    formación de Binance, así que cualquier consumidor de `candles` debe
    filtrarla explícitamente (bug #2, CLAUDE.md) — aquí se hace aunque sea
    solo para mostrar, para no reportar un precio que aún puede cambiar."""
    candles = await get_recent_candles(session, asset, "1h", 3)
    closed = [c for c in candles if c.close_time <= now]
    return closed[-1].close if closed else None


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


async def _open_positions_lines(session: AsyncSession, now: datetime) -> list[str]:
    positions = await get_open_positions_detail(session)
    if not positions:
        return ["  (ninguna)"]

    lines = []
    for pos in positions[:MAX_POSITION_ROWS]:
        held_h = (now - pos.entry_time).total_seconds() / 3600
        base = (
            f"  {pos.asset} [{pos.status}] {pos.timeframe or '-'}  qty={pos.qty}  "
            f"entry={pos.entry_price}  sl={pos.sl}  tp={pos.tp}  ({held_h:.1f}h)"
        )
        if pos.status == "pending":
            lines.append(f"{base}  zona={pos.entry_zone_low}-{pos.entry_zone_high}")
            continue
        last = await _last_closed_price(session, pos.asset, now)
        if last is None:
            lines.append(base)
            continue
        unrealized = (last - pos.entry_price) * pos.qty
        pct = (last - pos.entry_price) / pos.entry_price if pos.entry_price else Decimal("0")
        lines.append(f"{base}  last={last}  PnL_no_realizado={unrealized:.4f} USDT ({pct:.2%})")
    if len(positions) > MAX_POSITION_ROWS:
        lines.append(f"  ... y {len(positions) - MAX_POSITION_ROWS} más")
    return lines


async def build_daily_summary(session: AsyncSession, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_now = now.astimezone(settings.report_tzinfo)

    portfolio = await build_portfolio_snapshot(session, settings, settings.core_assets_list[0], now)
    state_info = await get_system_state_info(session)
    trades_count, trades_pnl = await _trades_today(session, day_start)
    trades_today = await get_closed_trades_history(session, limit=MAX_TRADE_ROWS, since_processed_at=day_start)
    entries_count, expired_count = await _entries_today(session, day_start)
    rejections = await _rejections_today(session, day_start)
    position_lines = await _open_positions_lines(session, now)
    core_lines = await _core_assets_status(session, settings, now)
    cumulative = await compute_closed_trades_summary(session)
    latest_candle_at = await get_latest_candle_time(session)

    if latest_candle_at is None:
        freshness = "sin velas todavía"
    else:
        age_h = (now - latest_candle_at).total_seconds() / 3600
        flag = "OK" if age_h <= STALE_AFTER_HOURS else "OBSOLETOS"
        freshness = f"{latest_candle_at:%Y-%m-%d %H:%M} UTC ({age_h:.1f}h, {flag})"

    # El motivo solo se muestra si el sistema esta REALMENTE en halt: el
    # rearme (`scripts/rearm.py`) pone state='running' pero deja
    # `halted_reason` con el valor viejo, asi que mostrarlo siempre
    # sacaria un motivo obsoleto junto a "running". Mismo criterio que
    # `scripts/estado.py::_print_system_state`.
    halt_note = (
        f" — {state_info.halted_reason}"
        if state_info.state == "halt" and state_info.halted_reason
        else ""
    )
    vetoes = await get_active_vetoes(session, settings, now)

    lines = [
        f"📊 <b>Informe diario</b> {now.date().isoformat()} (ventana día UTC)",
        f"Generado {local_now:%Y-%m-%d %H:%M %Z} · git {get_git_sha()[:8]}",
        "",
        "⚙️ <b>Sistema</b>",
        f"Sistema: {portfolio.system_state}{halt_note}",
        f"  Modo: {settings.mode} · entorno: {settings.environment} · cartera: {ENVIRONMENT}",
        f"  Última vela: {freshness}",
        "",
        "💰 <b>Cartera</b>",
        f"Equity: {portfolio.equity_quote:.2f} USDT  drawdown={portfolio.drawdown_pct:.2%}  "
        f"posiciones abiertas={portfolio.open_positions}",
        f"  Killswitch drawdown: {settings.drawdown_killswitch:.2%} · "
        f"límite pérdida diaria: {settings.daily_loss_limit:.2%}",
        "",
        f"📈 <b>Trades cerrados hoy: {trades_count}</b>  pnl total={trades_pnl:.4f} USDT",
    ]

    if trades_today:
        lines.extend(
            f"  {t.asset} {t.exit_type}  {t.entry_price} → {t.exit_price}  "
            f"{t.pnl_quote:+.4f} USDT ({t.pnl_pct_net:+.2%})"
            for t in trades_today
        )
    else:
        lines.append("  (ninguno)")

    lines.extend(
        [
            "",
            f"🧾 Órdenes registradas hoy: {entries_count} (expiradas sin fill: {expired_count})",
            "",
            "📌 <b>Posiciones abiertas / pendientes</b>",
        ]
    )
    lines.extend(position_lines)

    lines.extend(["", "Rechazos por motivo (hoy):"])
    if rejections:
        lines.extend(f"  {reason}: {count}" for reason, count in rejections)
    else:
        lines.append("  (ninguno)")

    lines.extend(
        [
            "",
            "🧠 <b>Fundamental</b>",
            f"  Vetos activos: {', '.join(vetoes) if vetoes else '(ninguno)'}",
            "",
            "Vigilancia core:",
        ]
    )
    lines.extend(core_lines)

    lines.extend(["", "📚 <b>Acumulado (paper, histórico completo)</b>"])
    if cumulative.n_trades == 0:
        lines.append("  Sin trades cerrados todavía")
    else:
        win_rate = f"{cumulative.win_rate:.1%}" if cumulative.win_rate is not None else "n/a"
        avg_pct = f"{cumulative.avg_pnl_pct:+.3%}" if cumulative.avg_pnl_pct is not None else "n/a"
        profit_factor = f"{cumulative.profit_factor:.2f}" if cumulative.profit_factor is not None else "n/a"
        lines.append(
            f"  Trades: {cumulative.n_trades}  win rate={win_rate}  "
            f"expectancy={avg_pct}  profit factor={profit_factor}  "
            f"pnl={cumulative.total_pnl_quote:+.2f} USDT"
        )

    return "\n".join(lines)


def save_report(text: str, settings: Settings, now: datetime) -> Path | None:
    """Copia en disco del informe (`reports_dir/informe-YYYY-MM-DD.md`).

    # DECISION (2026-09-04): FAIL-OPEN, igual que Telegram — un disco
    # lleno o un permiso mal puesto en el VPS no debe tumbar el job ni,
    # sobre todo, impedir que la notificación se envíe. Se loguea y se
    # sigue. `reports_dir` vacío desactiva la copia por completo.
    """
    if not settings.reports_dir:
        return None
    try:
        directory = Path(settings.reports_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"informe-{now.date().isoformat()}.md"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        logger.exception("daily_report.save_failed", reports_dir=settings.reports_dir)
        return None


def next_report_run(settings: Settings, now: datetime) -> datetime:
    """Próximo disparo del informe en hora LOCAL (`report_timezone`),
    devuelto en UTC. Solo informativo (logs/CLI): el cron real lo arma
    APScheduler en `app/scheduler.py` con la misma configuración."""
    local_now = now.astimezone(settings.report_tzinfo)
    target = local_now.replace(
        hour=settings.daily_report_hour, minute=settings.daily_report_minute, second=0, microsecond=0
    )
    if target <= local_now:
        target = target + timedelta(days=1)
    return target.astimezone(UTC)
