"""Paper ledger interno — simula fills y salidas de posición sobre velas
reales de mercado, SIN llamar a ningún exchange (ni testnet ni producción)
para ejecutar.

# DECISION (fase 1): la sección 10.1 del documento asume que `paper_ledger`
# calcula equity a partir de fills REALES contra Binance Spot Testnet, con
# fee simulada. Mientras no existan credenciales de testnet
# (BINANCE_API_KEY/SECRET), este módulo sustituye esa pieza con una
# simulación pura: cuando el risk engine aprueba una entrada, se abre una
# posición de papel al `entry_ref` de la señal y se sigue vela a vela
# (velas ya ingeridas, mismo dato que ve el scanner) hasta que toca SL, TP,
# se invalida técnicamente o expira por horizonte de tiempo. Fee de
# 0.1%/lado (`settings.taker_fee`) igual que el backtest (sección 14) y que
# el `paper_ledger` original (sección 10.1).
#
# Al escribir filas con la misma forma que usaría un executor real
# (`trade_entries`/`trade_exits`/`equity_snapshots`), los checks de cartera
# de `services/risk/portfolio_state.py` (max_positions, cooldowns,
# drawdown_killswitch, daily_loss_limit) se activan sin ningún cambio ahí.
#
# Fuera de alcance explícito: redondeo a filtros de exchange (tickSize/
# stepSize/minNotional), veto fundamental (fase 2+), reconciliación — todo
# eso solo tiene sentido contra un exchange real (ver executor futuro,
# sección 10).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import HorizonClass, TradeStatus
from core.logging import get_logger
from core.schemas.market import Candle
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from db.models import EquitySnapshot, PositionEvent, TradeEntry, TradeExit
from notifications.telegram import send_message
from services.data.persistence import get_recent_candles
from services.risk.portfolio_state import (
    ENVIRONMENT,
    compute_drawdown_pct,
    get_latest_equity,
)

logger = get_logger("paper_ledger")


class PaperEntryLike(Protocol):
    """Atributos que `evaluate_exit` necesita de una entrada de papel.
    `TradeEntry` (ORM) los cumple estructuralmente; `backtests/
    strategy_breakout.py` reutiliza `evaluate_exit` tal cual pasando un
    dataclass ligero que también los cumple — mismo código de decisión de
    salida en vivo y en backtest (regla crítica, sección 6)."""

    entry_time: datetime
    entry_price: Decimal
    sl: Decimal
    tp: Decimal
    invalidation_level: Decimal | None
    horizon_class: str | None


async def open_position(
    session: AsyncSession,
    settings: Settings,
    *,
    decision_log_id: int,
    asset: str,
    technical_signal: TechnicalSignal,
    risk_verdict: RiskVerdict,
    entry_price: Decimal,
    now: datetime,
) -> TradeEntry:
    """Abre una posición de papel para una entrada ya aprobada por el risk
    engine (`risk_verdict.approved=True`). No hay orden real ni exchange de
    por medio: solo se persiste la posición para hacerle seguimiento."""
    if risk_verdict.size_quote is None:
        raise ValueError("open_position requiere un RiskVerdict aprobado (size_quote no puede ser None)")

    qty = risk_verdict.size_quote / entry_price
    entry = TradeEntry(
        decision_log_id=decision_log_id,
        asset=asset,
        environment=ENVIRONMENT,
        client_order_id=f"paper-{decision_log_id}",
        exchange_order_id=None,
        entry_time=now,
        entry_price=entry_price,
        qty=qty,
        tp=technical_signal.take_profit,
        sl=technical_signal.stop_loss,
        oco_list_id=None,
        status=TradeStatus.open.value,
        timeframe=technical_signal.timeframe,
        horizon_class=technical_signal.horizon_class.value,
        invalidation_level=technical_signal.invalidation_level,
    )
    session.add(entry)
    await session.flush()
    session.add(
        PositionEvent(
            trade_entry_id=entry.id,
            ts=now,
            event_type="paper_open",
            payload_jsonb={
                "asset": asset,
                "entry_price": str(entry_price),
                "qty": str(qty),
                "tp": str(technical_signal.take_profit),
                "sl": str(technical_signal.stop_loss),
            },
        )
    )
    logger.info("paper.open", asset=asset, trade_entry_id=entry.id, entry_price=str(entry_price))
    await send_message(
        settings,
        f"🟢 <b>Nueva posición de papel</b> {asset}\n"
        f"entry={entry_price}  sl={technical_signal.stop_loss}  tp={technical_signal.take_profit}\n"
        f"qty={qty}",
    )
    return entry


@dataclass
class ExitDecision:
    exit_time: datetime
    exit_price: Decimal
    exit_type: str


def max_hold_for_horizon(settings: Settings, horizon_class: str) -> timedelta:
    """Única fuente de verdad del horizonte máximo de una posición (sección
    10.1): 48h para señales intraday (1h), 7 días para swing (4h).
    Reutilizada por `backtests/strategy_breakout.py` para acotar la
    ventana de simulación de salida — mismo horizonte que en vivo."""
    if horizon_class == HorizonClass.hours.value:
        return timedelta(hours=settings.max_hold_hours_intraday)
    return timedelta(days=settings.max_hold_days_swing)


def evaluate_exit(
    entry: PaperEntryLike,
    candles: list[Candle],
    settings: Settings,
    now: datetime,
) -> ExitDecision | None:
    """Vela a vela (ascendente, solo velas CERRADAS con `open_time >
    entry_time`, del mismo timeframe que la señal): ¿toca SL, TP o se
    invalida técnicamente? SL se comprueba antes que TP si ambos se tocan
    en la misma vela — criterio conservador, igual que documenta el
    backtest (sección 14) para el caso ambiguo intra-vela. Si ninguna vela
    dispara nada, se comprueba la salida por tiempo (horizonte de la señal)
    contra `now`, no contra velas.

    # FIX (2026-07-06, ver CHANGELOG): se excluye la vela en curso
    # (`close_time > now`) — mismo criterio que `candles_to_frame`
    # (anti look-ahead, sección 18). Antes, la kline en formación que
    # Binance devuelve (y que la ingesta upsertea) se evaluaba: la
    # invalidación usaba un `close` aún no definitivo (contradice la regla
    # "invalidación por CIERRE") y `exit_time` podía quedar en el futuro
    # (`close_time` reconstruido = open_time + duración > now)."""
    relevant = [
        c for c in candles if c.open_time > entry.entry_time and c.close_time <= now
    ]
    for candle in relevant:
        if candle.low <= entry.sl:
            return ExitDecision(candle.close_time, entry.sl, TradeStatus.closed_sl.value)
        if candle.high >= entry.tp:
            return ExitDecision(candle.close_time, entry.tp, TradeStatus.closed_tp.value)
        if entry.invalidation_level is not None and candle.close < entry.invalidation_level:
            return ExitDecision(candle.close_time, candle.close, TradeStatus.closed_invalidated.value)

    if entry.horizon_class is not None:
        max_hold = max_hold_for_horizon(settings, entry.horizon_class)
        if now - entry.entry_time >= max_hold:
            last_close = relevant[-1].close if relevant else entry.entry_price
            return ExitDecision(now, last_close, TradeStatus.closed_time.value)

    return None


def compute_trade_pnl(
    entry_price: Decimal, exit_price: Decimal, qty: Decimal, taker_fee: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Fees/PnL de un trade cerrado — fee de `taker_fee` por lado (entrada
    + salida), igual que `sección 10.1`/`14`. Única fórmula, reutilizada
    por `close_position` (paper ledger) y por `backtests/
    strategy_breakout.py::simulate_trades` (con `qty=1` para el retorno a
    nivel de instrumento) para no duplicarla (regla crítica, sección 6).
    Devuelve `(fees_paid, pnl_quote, pnl_pct_net)`."""
    entry_notional = qty * entry_price
    exit_notional = qty * exit_price
    fees_paid = taker_fee * (entry_notional + exit_notional)
    pnl_quote = exit_notional - entry_notional - fees_paid
    pnl_pct_net = pnl_quote / entry_notional if entry_notional > 0 else Decimal("0")
    return fees_paid, pnl_quote, pnl_pct_net


async def close_position(
    session: AsyncSession,
    settings: Settings,
    entry: TradeEntry,
    exit_decision: ExitDecision,
    now: datetime,
) -> TradeExit:
    """`now` es el momento de PROCESO del cierre y es el `ts` del
    `EquitySnapshot` que se inserta.

    # FIX (2026-07-06, ver CHANGELOG): antes el snapshot usaba
    # `ts=exit_decision.exit_time` (el close de la vela, potencialmente
    # horas en el pasado) y `get_latest_equity` ordenaba por `ts`: si dos
    # cierres se procesaban en el mismo ciclo con exit_times no
    # cronológicos, el snapshot "más antiguo" quedaba invisible y su PnL
    # desaparecía de la curva de equity (y del drawdown/killswitch).
    # `trade_exits.exit_time` y el `position_event` conservan el tiempo de
    # la vela (cuándo ocurrió la salida); solo la curva de equity usa el
    # tiempo de proceso, que es monotónico."""
    fees_paid, pnl_quote, pnl_pct_net = compute_trade_pnl(
        entry.entry_price, exit_decision.exit_price, entry.qty, settings.taker_fee
    )

    exit_row = TradeExit(
        trade_entry_id=entry.id,
        exit_time=exit_decision.exit_time,
        exit_price=exit_decision.exit_price,
        exit_qty=entry.qty,
        exit_type=exit_decision.exit_type,
        fees_paid=fees_paid,
        pnl_quote=pnl_quote,
        pnl_pct_net=pnl_pct_net,
    )
    session.add(exit_row)
    entry.status = exit_decision.exit_type

    session.add(
        PositionEvent(
            trade_entry_id=entry.id,
            ts=exit_decision.exit_time,
            event_type="paper_exit",
            payload_jsonb={
                "exit_type": exit_decision.exit_type,
                "exit_price": str(exit_decision.exit_price),
                "pnl_quote": str(pnl_quote),
                "pnl_pct_net": str(pnl_pct_net),
            },
        )
    )

    latest_equity = await get_latest_equity(session, settings)
    new_equity = latest_equity + pnl_quote
    drawdown_pct = await compute_drawdown_pct(session, new_equity)
    open_result = await session.execute(
        select(TradeEntry.id).where(TradeEntry.environment == ENVIRONMENT, TradeEntry.status == TradeStatus.open.value)
    )
    open_positions_remaining = len(open_result.all())
    session.add(
        EquitySnapshot(
            ts=now,
            environment=ENVIRONMENT,
            equity_quote=new_equity,
            open_positions=open_positions_remaining,
            drawdown_pct=drawdown_pct,
        )
    )

    logger.info(
        "paper.close",
        trade_entry_id=entry.id,
        exit_type=exit_decision.exit_type,
        pnl_quote=str(pnl_quote),
        new_equity=str(new_equity),
    )
    emoji = "✅" if pnl_quote > 0 else "🔴"
    await send_message(
        settings,
        f"{emoji} <b>Cierre de papel</b> {entry.asset} ({exit_decision.exit_type})\n"
        f"exit={exit_decision.exit_price}  pnl={pnl_quote:.4f} USDT ({pnl_pct_net:.2%})\n"
        f"equity={new_equity:.2f} USDT  drawdown={drawdown_pct:.2%}",
    )
    return exit_row


async def update_open_positions(session: AsyncSession, settings: Settings, now: datetime) -> dict[str, int]:
    """Job de seguimiento (sección 11, simplificado a la cadencia del ciclo
    de 15 min en vez de los 5 min originales — pensados para el monitor
    real contra testnet). Recorre las posiciones de papel abiertas y las
    cierra si corresponde."""
    stmt = select(TradeEntry).where(TradeEntry.environment == ENVIRONMENT, TradeEntry.status == TradeStatus.open.value)
    result = await session.execute(stmt)
    open_entries = list(result.scalars().all())

    counts = {"checked": len(open_entries), "closed": 0}
    for entry in open_entries:
        if entry.timeframe is None:
            continue
        candles = await get_recent_candles(session, entry.asset, entry.timeframe, 500)
        exit_decision = evaluate_exit(entry, candles, settings, now)
        if exit_decision is not None:
            await close_position(session, settings, entry, exit_decision, now)
            counts["closed"] += 1

    return counts
