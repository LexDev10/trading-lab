"""Paper ledger interno — simula fills y salidas de posición sobre velas
reales de mercado, SIN llamar a ningún exchange (ni testnet ni producción)
para ejecutar.

# DECISION (fase 1): la sección 10.1 del documento asume que `paper_ledger`
# calcula equity a partir de fills REALES contra Binance Spot Testnet, con
# fee simulada. Mientras no existan credenciales de testnet
# (BINANCE_API_KEY/SECRET), este módulo sustituye esa pieza con una
# simulación pura: cuando el risk engine aprueba una entrada, se registra
# una ORDEN PENDIENTE de papel (orden límite simulada, ver
# `evaluate_pending_fill`) y se sigue vela a vela (velas ya ingeridas,
# mismo dato que ve el scanner) hasta que llena, expira, o — ya llena —
# toca SL, TP, se invalida técnicamente o expira por horizonte de tiempo.
# Fee de 0.1%/lado (`settings.taker_fee`) igual que el backtest (sección
# 14) y que el `paper_ledger` original (sección 10.1).
#
# Al escribir filas con la misma forma que usaría un executor real
# (`trade_entries`/`trade_exits`/`equity_snapshots`), los checks de cartera
# de `services/risk/portfolio_state.py` (max_positions, cooldowns,
# drawdown_killswitch, daily_loss_limit) se activan sin ningún cambio ahí.
#
# Fuera de alcance explícito: redondeo a filtros de exchange (tickSize/
# stepSize/minNotional), reconciliación — todo eso solo tiene sentido
# contra un exchange real (ver executor futuro, sección 10).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import HorizonClass, TradeStatus
from core.logging import get_logger
from core.schemas.market import Candle
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from db.models import EquitySnapshot, PositionEvent, TradeEntry, TradeExit
from services.data.persistence import get_recent_candles
from services.fundamental.veto import asset_has_active_closing_veto
from services.risk.portfolio_state import (
    ENVIRONMENT,
    compute_drawdown_pct,
    get_latest_equity,
)

logger = get_logger("paper_ledger")


class PaperEntryLike(Protocol):
    """Atributos que `evaluate_exit` necesita de una entrada YA LLENA
    (`status='open'`). `TradeEntry` (ORM) los cumple estructuralmente;
    `backtests/strategy_breakout.py` reutiliza `evaluate_exit` tal cual
    pasando un dataclass ligero que también los cumple — mismo código de
    decisión de salida en vivo y en backtest (regla crítica, sección 6)."""

    entry_time: datetime
    entry_price: Decimal
    sl: Decimal
    tp: Decimal
    invalidation_level: Decimal | None
    horizon_class: str | None


class PendingEntryLike(Protocol):
    """Atributos que `evaluate_pending_fill` necesita de una orden
    pendiente (`status='pending'`) — antes del fill, `entry_time` es el
    momento en que se registró la señal (no un fill real todavía).
    `entry_zone_high` es `Decimal | None` porque la columna ORM es
    nullable (filas anteriores a este fix, o entradas ya `open`/cerradas
    que nunca la tuvieron) — `evaluate_pending_fill` la comprueba."""

    entry_time: datetime
    entry_zone_high: Decimal | None
    sl: Decimal


async def signal_already_traded(
    session: AsyncSession, asset: str, timeframe: str, signal_candle_close_time: datetime
) -> bool:
    """FIX (2026-07-07, bug #10 CODE_REVIEW_2026-07-07.md), segunda capa
    de dedupe: ¿ya existe una `trade_entry` (cualquier status, mismo
    environment) para esta MISMA vela de señal? El check de cartera
    (`PortfolioSnapshot.asset_has_open_position`) ya bloquea reentradas
    mientras la posición sigue pendiente/abierta; este check adicional
    cubre el caso de una posición YA CERRADA muy rápido dentro de la
    ventana de cooldown normal donde, por el motivo que sea, se quisiera
    reevaluar — la MISMA vela de ruptura nunca debe volver a abrir una
    segunda entrada, sea cual sea su estado final."""
    stmt = select(
        exists().where(
            TradeEntry.environment == ENVIRONMENT,
            TradeEntry.asset == asset,
            TradeEntry.timeframe == timeframe,
            TradeEntry.signal_candle_close_time == signal_candle_close_time,
        )
    )
    result = await session.execute(stmt)
    return bool(result.scalar())


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
) -> tuple[TradeEntry, str]:
    """Registra una ORDEN PENDIENTE de papel (límite simulada, sección
    10.1) para una entrada ya aprobada por el risk engine
    (`risk_verdict.approved=True`). No hay fill inmediato ni exchange de
    por medio: solo se persiste la orden para hacerle seguimiento.

    # FIX (2026-07-07, bug #11 CODE_REVIEW_2026-07-07.md): antes esta
    # función abría la posición YA LLENA a `entry_price` (el punto medio
    # `entry_ref` de la zona de entrada, sección 7.2) — un precio que el
    # mercado nunca confirmó. En una estrategia de breakout eso sesga
    # sistemáticamente a favor: los mejores breakouts no retroceden, así
    # que en real esos trades no se habrían llenado o se habrían llenado
    # peor. Ahora la entrada se crea en `status='pending'`; el fill real
    # lo decide `evaluate_pending_fill` vela a vela, respetando
    # `settings.entry_ttl_minutes` (declarado en `Settings` desde el
    # origen del proyecto pero nunca usado hasta este fix).
    #
    # `entry_price`/`qty` en este momento son PROVISIONALES (dimensionados
    # contra el `entry_ref` teórico): al fill real, `update_open_positions`
    # recalcula `qty` contra el precio de fill efectivo — el `size_quote`
    # aprobado por el risk engine es lo que se mantiene fijo, no la
    # cantidad de moneda.
    #
    # FIX (2026-07-07, bug #15): ya no envía Telegram aquí — devuelve el
    # texto para que el CALLER lo envíe DESPUÉS de `session.commit()`
    # (evita notificar una orden que un fallo posterior en el mismo ciclo
    # termina revirtiendo)."""
    if risk_verdict.size_quote is None:
        raise ValueError("open_position requiere un RiskVerdict aprobado (size_quote no puede ser None)")

    qty = risk_verdict.size_quote / entry_price
    entry_low, entry_high = technical_signal.entry_zone
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
        status=TradeStatus.pending.value,
        timeframe=technical_signal.timeframe,
        horizon_class=technical_signal.horizon_class.value,
        invalidation_level=technical_signal.invalidation_level,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        signal_candle_close_time=technical_signal.candle_close_time,
    )
    session.add(entry)
    await session.flush()
    session.add(
        PositionEvent(
            trade_entry_id=entry.id,
            ts=now,
            event_type="paper_pending_open",
            payload_jsonb={
                "asset": asset,
                "entry_zone_low": str(entry_low),
                "entry_zone_high": str(entry_high),
                "tp": str(technical_signal.take_profit),
                "sl": str(technical_signal.stop_loss),
            },
        )
    )
    logger.info(
        "paper.pending_open", asset=asset, trade_entry_id=entry.id,
        entry_zone_low=str(entry_low), entry_zone_high=str(entry_high),
    )
    message = (
        f"🟡 <b>Orden pendiente de papel</b> {asset}\n"
        f"zona entrada={entry_low}-{entry_high}  sl={technical_signal.stop_loss}  tp={technical_signal.take_profit}\n"
        f"expira en {settings.entry_ttl_minutes} min si no hay pullback a la zona"
    )
    return entry, message


@dataclass
class FillDecision:
    fill_time: datetime
    fill_price: Decimal
    candle_low: Decimal


def evaluate_pending_fill(
    entry: PendingEntryLike, candles: list[Candle], settings: Settings, now: datetime
) -> FillDecision | None:
    """FIX (2026-07-07, bug #11): ¿ha llenado ya la orden límite simulada?
    Vela a vela (ascendente, solo velas CERRADAS con `open_time >
    entry_time` y `close_time <= now` — mismo criterio anti look-ahead
    que `evaluate_exit`/`candles_to_frame`): si `candle.low <=
    entry_zone_high`, el precio tocó la zona de entrada y la orden llena.

    Precio de fill = `min(entry_zone_high, candle.open)`: modelo estándar
    de una orden límite — si la vela abre ya dentro o por debajo de la
    zona (gap), el fill real habría ocurrido al open (mejor precio que el
    límite, que el mercado sí ofreció); si no, al límite mismo
    (`entry_zone_high`). Nunca se asume un fill al punto medio de la vela
    ni al `low` (eso sería asumir la mejor ejecución posible, que es
    precisamente el sesgo que causó este bug: un precio que el mercado no
    confirmó de verdad).

    El caller (`update_open_positions`) es responsable de expirar la
    orden si no hay fill dentro de `settings.entry_ttl_minutes`.

    # DECISION (2026-07-07): a diferencia de `evaluate_exit` (que excluye
    # la vela en curso porque decide sobre datos que deben ser
    # DEFINITIVOS — invalidación por CIERRE, horizonte de tiempo), el fill
    # de una orden límite es un evento de mercado puntual: si el precio ya
    # tocó la zona, el fill ya ocurrió, haya cerrado la vela o no. La
    # ingesta persiste la kline en formación de Binance con su low/high
    # REAL hasta ese momento (dato observado, no una previsión) — por eso
    # aquí SÍ se admite la vela en curso (`open_time <= now`, sin exigir
    # `close_time <= now`). Es además indispensable: `entry_ttl_minutes`
    # (45 por defecto) es MENOR que la duración de cualquier timeframe
    # operado (1h/4h) — exigir el cierre de la vela para comprobar el fill
    # haría que la orden expirase SIEMPRE antes de que exista una sola
    # vela cerrada que comprobar, y ninguna posición se abriría jamás.
    # También se usa `open_time >= entry_time` (inclusive, no estricto):
    # la vela INMEDIATAMENTE siguiente a la señal (misma frontera:
    # `open_time` de esa vela == `entry_time`, el `close_time` de la vela
    # de la señal) sí debe poder disparar el fill — a diferencia de
    # `evaluate_exit`, aquí no hay ningún dato de esa vela ya usado para
    # decidir la señal misma que debiera excluirse."""
    if entry.entry_zone_high is None:
        return None
    entry_zone_high = entry.entry_zone_high
    relevant = [c for c in candles if c.open_time >= entry.entry_time and c.open_time <= now]
    for candle in relevant:
        if candle.low <= entry_zone_high:
            fill_price = min(entry_zone_high, candle.open)
            fill_time = min(candle.close_time, now)
            return FillDecision(fill_time=fill_time, fill_price=fill_price, candle_low=candle.low)
    return None


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
    veto_active: bool = False,
) -> ExitDecision | None:
    """Vela a vela (ascendente, solo velas CERRADAS con `open_time >
    entry_time`, del mismo timeframe que la señal): ¿toca SL, TP o se
    invalida técnicamente? SL se comprueba antes que TP si ambos se tocan
    en la misma vela — criterio conservador, igual que documenta el
    backtest (sección 14) para el caso ambiguo intra-vela. Si ninguna vela
    dispara nada, se comprueba primero el veto fundamental (si está
    activo) y luego la salida por tiempo (horizonte de la señal) contra
    `now`, no contra velas.

    # FIX (2026-07-06, ver CHANGELOG): se excluye la vela en curso
    # (`close_time > now`) — mismo criterio que `candles_to_frame`
    # (anti look-ahead, sección 18). Antes, la kline en formación que
    # Binance devuelve (y que la ingesta upsertea) se evaluaba: la
    # invalidación usaba un `close` aún no definitivo (contradice la regla
    # "invalidación por CIERRE") y `exit_time` podía quedar en el futuro
    # (`close_time` reconstruido = open_time + duración > now).

    `veto_active` (sección 12.4, fase 2, criterio de cierre forzoso — ver
    `services/fundamental/veto.py::asset_has_active_closing_veto`):

    # FIX (2026-07-07, bug #16 CODE_REVIEW_2026-07-07.md): antes el veto
    # se comprobaba ANTES del bucle de velas, así que pisaba un SL/TP/
    # invalidación que ya había ocurrido en una vela anterior dentro de
    # `relevant` — el exit registrado (tipo y precio) era el del veto, no
    # el que realmente ocurrió primero en el tiempo, distorsionando PnL y
    # atribución por `exit_type`. Ahora el veto solo cierra si NINGUNA
    # vela cerrada disparó SL/TP/invalidación antes — sigue siendo más
    # urgente que la salida por tiempo (se comprueba antes), pero nunca
    # reescribe una salida que el mercado ya había decidido. Al último
    # close disponible, o `entry_price` si todavía no hay ninguna vela
    # cerrada tras la entrada (mismo fallback que la salida por tiempo).
    # El caller (`update_open_positions`) calcula el flag con
    # `asset_has_active_closing_veto`; el backtest nunca lo activa (la
    # capa fundamental no se backtestea, sección 14)."""
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

    if veto_active:
        last_close = relevant[-1].close if relevant else entry.entry_price
        return ExitDecision(now, last_close, TradeStatus.closed_fundamental_veto.value)

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
) -> tuple[TradeExit, str]:
    """`now` es el momento de PROCESO del cierre: es el `ts` del
    `EquitySnapshot` que se inserta y (FIX 2026-07-07, bug #17) el
    `processed_at` de `trade_exits`.

    # FIX (2026-07-06, ver CHANGELOG): antes el snapshot usaba
    # `ts=exit_decision.exit_time` (el close de la vela, potencialmente
    # horas en el pasado) y `get_latest_equity` ordenaba por `ts`: si dos
    # cierres se procesaban en el mismo ciclo con exit_times no
    # cronológicos, el snapshot "más antiguo" quedaba invisible y su PnL
    # desaparecía de la curva de equity (y del drawdown/killswitch).
    # `trade_exits.exit_time` y el `position_event` conservan el tiempo de
    # la vela (cuándo ocurrió la salida); solo la curva de equity usa el
    # tiempo de proceso, que es monotónico.
    #
    # FIX (2026-07-07, bug #17): `daily_loss_limit`
    # (`portfolio_state._get_daily_realized_pnl_pct`) tenía el mismo
    # problema que el bug #1: agregaba por `exit_time` (tiempo de vela),
    # así que un cierre procesado hoy con `exit_time` de ayer no contaba
    # en la pérdida de HOY. `processed_at=now` es monotónico igual que el
    # `ts` del `EquitySnapshot` — mismo criterio, columna nueva porque
    # `trade_exits` no tenía ninguna con esa semántica.
    #
    # FIX (2026-07-07, bug #15): ya no envía Telegram aquí — devuelve el
    # texto para que el CALLER lo envíe DESPUÉS de `session.commit()`."""
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
        processed_at=now,
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
    message = (
        f"{emoji} <b>Cierre de papel</b> {entry.asset} ({exit_decision.exit_type})\n"
        f"exit={exit_decision.exit_price}  pnl={pnl_quote:.4f} USDT ({pnl_pct_net:.2%})\n"
        f"equity={new_equity:.2f} USDT  drawdown={drawdown_pct:.2%}"
    )
    return exit_row, message


async def _process_pending_entry(
    session: AsyncSession, settings: Settings, entry: TradeEntry, now: datetime
) -> str | None:
    """Una orden pendiente puede: llenar (pasa a `open`, o directamente a
    `closed_sl` si la MISMA vela que llena también toca el SL — criterio
    conservador documentado en el bug #11: se asume la peor secuencia
    intra-vela, no la mejor), o expirar sin fill tras
    `settings.entry_ttl_minutes` (`status='expired'`, sin trade_exit ni
    impacto en equity — nunca existió una posición real)."""
    if entry.timeframe is None or entry.entry_zone_high is None:
        return None

    candles = await get_recent_candles(session, entry.asset, entry.timeframe, 500)
    fill = evaluate_pending_fill(entry, candles, settings, now)

    if fill is not None:
        size_quote = entry.qty * entry.entry_price
        fill_qty = size_quote / fill.fill_price
        entry.entry_time = fill.fill_time
        entry.entry_price = fill.fill_price
        entry.qty = fill_qty

        if fill.candle_low <= entry.sl:
            # FIX (2026-07-07, bug #11), DECISION documentada: si la MISMA
            # vela que llena la orden también toca el SL, se cierra de
            # inmediato como SL (pérdida completa) — criterio conservador,
            # no se asume que hubo tiempo de reaccionar entre el fill y el
            # stop dentro de la misma vela.
            entry.status = TradeStatus.open.value
            _, message = await close_position(
                session, settings, entry,
                ExitDecision(fill.fill_time, entry.sl, TradeStatus.closed_sl.value),
                now,
            )
            return message

        entry.status = TradeStatus.open.value
        session.add(
            PositionEvent(
                trade_entry_id=entry.id, ts=fill.fill_time, event_type="paper_filled",
                payload_jsonb={"fill_price": str(fill.fill_price), "qty": str(fill_qty)},
            )
        )
        logger.info("paper.filled", asset=entry.asset, trade_entry_id=entry.id, fill_price=str(fill.fill_price))
        return (
            f"🟢 <b>Fill de papel</b> {entry.asset}\n"
            f"entry={fill.fill_price}  sl={entry.sl}  tp={entry.tp}\n"
            f"qty={fill_qty}"
        )

    if now - entry.entry_time >= timedelta(minutes=settings.entry_ttl_minutes):
        entry.status = TradeStatus.expired.value
        session.add(
            PositionEvent(trade_entry_id=entry.id, ts=now, event_type="paper_expired", payload_jsonb={})
        )
        logger.info("paper.expired", asset=entry.asset, trade_entry_id=entry.id)
        return f"⚪ <b>Orden expirada</b> {entry.asset}: sin pullback a la zona de entrada en {settings.entry_ttl_minutes} min"

    return None


async def update_open_positions(
    session: AsyncSession, settings: Settings, now: datetime
) -> tuple[dict[str, int], list[str]]:
    """Job de seguimiento (sección 11, simplificado a la cadencia del ciclo
    de 15 min en vez de los 5 min originales — pensados para el monitor
    real contra testnet). Procesa primero las órdenes PENDIENTES (fill o
    expiración, bug #11) y luego las posiciones ya abiertas (salidas).

    # FIX (2026-07-07, bug #15): devuelve también los mensajes de
    # Telegram en vez de enviarlos aquí — el CALLER (scheduler) los envía
    # DESPUÉS de `session.commit()`."""
    messages: list[str] = []

    pending_stmt = select(TradeEntry).where(
        TradeEntry.environment == ENVIRONMENT, TradeEntry.status == TradeStatus.pending.value
    )
    pending_entries = list((await session.execute(pending_stmt)).scalars().all())

    counts = {"checked": len(pending_entries), "filled": 0, "expired": 0, "closed": 0}
    for entry in pending_entries:
        message = await _process_pending_entry(session, settings, entry, now)
        if message is not None:
            messages.append(message)
            if entry.status == TradeStatus.expired.value:
                counts["expired"] += 1
            elif entry.status == TradeStatus.open.value:
                counts["filled"] += 1
            else:
                counts["closed"] += 1

    open_stmt = select(TradeEntry).where(TradeEntry.environment == ENVIRONMENT, TradeEntry.status == TradeStatus.open.value)
    open_entries = list((await session.execute(open_stmt)).scalars().all())
    counts["checked"] += len(open_entries)

    for entry in open_entries:
        if entry.timeframe is None:
            continue
        candles = await get_recent_candles(session, entry.asset, entry.timeframe, 500)
        # FIX (2026-07-07, bug #18): el cierre forzoso exige corroboración
        # de fuentes NEWS independientes (`asset_has_active_closing_veto`)
        # — un veto de fuente social (Reddit) ya no cierra posiciones por
        # sí solo, solo bloquea entradas nuevas (risk engine).
        veto_active = await asset_has_active_closing_veto(session, entry.asset.removesuffix("USDT"), settings, now)
        exit_decision = evaluate_exit(entry, candles, settings, now, veto_active=veto_active)
        if exit_decision is not None:
            _, message = await close_position(session, settings, entry, exit_decision, now)
            messages.append(message)
            counts["closed"] += 1

    return counts, messages
