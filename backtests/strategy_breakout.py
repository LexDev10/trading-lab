"""Señales de entrada + simulación de salida para el backtest, reutilizando
LA MISMA lógica que `services/technical/setups.py` + `signal_builder.py`
(entradas) y `services/execution/paper_ledger.py` (fill pendiente +
salidas) — regla crítica, sección 6: "la lógica de señales... debe ser el
mismo código importado, no dos implementaciones. Divergencia backtest/live
es un bug de primera clase.".

# FIX (2026-07-06, ver CHANGELOG, bug #4): antes `run_portfolio` delegaba
# la simulación de salidas a `vectorbt.Portfolio.from_signals(sl_stop=...,
# tp_stop=...)`, que SOLO modela SL/TP — ciego a la invalidación técnica y
# a la salida por tiempo que sí aplica `paper_ledger.evaluate_exit` (y que
# en la práctica dispara mucho más a menudo que el SL, porque la entrada
# queda por encima de `invalidation_level`). `simulate_trades` reemplaza
# esa simulación: para cada señal, llama directamente a
# `paper_ledger.evaluate_exit` (mismo código, no una reimplementación) para
# decidir cuándo y a qué precio sale. `vectorbt` deja de usarse aquí — no
# aportaba nada que no podamos calcular de forma más fiel con la misma
# fórmula de fees que ya usa el paper ledger (`compute_trade_pnl`).

# FIX (2026-07-07, bug #11 CODE_REVIEW_2026-07-07.md): antes cada señal
# entraba INMEDIATAMENTE a `entry_ref` (el punto medio de la zona de
# entrada), un precio que el mercado nunca confirmó — sesgo sistemático a
# favor. `simulate_trades` ahora llama a
# `paper_ledger.evaluate_pending_fill` (mismo código que el paper ledger en
# vivo) para decidir si y cuándo habría hecho fill una orden límite
# simulada dentro de `settings.entry_ttl_minutes`; si no hay fill, la señal
# se descarta (equivale a `expired`) — la expectancy resultante puede ser
# menor que antes de este fix: es información real, no un bug del fix."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import pandas as pd

from app.config import Settings
from core.schemas.market import Candle
from services.execution.paper_ledger import (
    ExitDecision,
    compute_trade_pnl,
    evaluate_exit,
    evaluate_pending_fill,
    max_hold_for_horizon,
)
from services.technical.setups import compute_breakout_frame
from services.technical.signal_builder import GROSS_RR_TARGET, STOP_ATR_BUFFER, horizon_for_timeframe

GROSS_RR_TARGET_F = float(GROSS_RR_TARGET)
STOP_ATR_BUFFER_F = float(STOP_ATR_BUFFER)

# Sección 14: slippage pesimista (aproximado como 2bps uniformes; el tick
# size real varía por símbolo y no se modela aquí). Solo aplica al
# backtest — el paper ledger no lo necesita porque simula fills contra
# precios reales de vela, no una ejecución hipotética contra order book.
DEFAULT_SLIPPAGE = 0.0002

TRADES_COLUMNS = [
    "Entry Timestamp",
    "Exit Timestamp",
    "exit_type",
    "Avg Entry Price",
    "Avg Exit Price",
    "Return",
    "sl_pct_at_entry",
]


def generate_signals(df: pd.DataFrame, lookback: int, volume_confirm_mult: float) -> pd.DataFrame:
    """Devuelve un DataFrame alineado con `df` (mismo índice) con columnas
    `entries` (bool), `sl_pct`/`tp_pct` (fracción del precio de entrada
    TEÓRICO `entry_ref`, informativas — el sizing real usa `sl_pct_at_entry`
    calculado en `simulate_trades` contra el precio de FILL efectivo) y los
    precios absolutos `entry_low`/`entry_high` (zona de entrada, misma
    fórmula que `signal_builder.build_technical_signal`) /`sl`/`tp`/
    `invalidation_level` que necesita `simulate_trades`."""
    frame = compute_breakout_frame(df, lookback, volume_confirm_mult)

    close = df["close"]
    range_high = frame["range_high"]
    entry_low = pd.concat([range_high, close], axis=1).min(axis=1)
    entry_high = pd.concat([range_high, close], axis=1).max(axis=1)
    entry_ref = (entry_low + entry_high) / 2
    stop_loss = frame["range_low"] - STOP_ATR_BUFFER_F * frame["atr_14"]
    risk_distance = entry_ref - stop_loss
    take_profit = entry_ref + GROSS_RR_TARGET_F * risk_distance

    valid_entry = frame["breakout"] & (risk_distance > 0) & (entry_ref > 0)

    sl_pct = (risk_distance / entry_ref).where(valid_entry, other=0.0)
    tp_pct = (GROSS_RR_TARGET_F * risk_distance / entry_ref).where(valid_entry, other=0.0)

    return pd.DataFrame(
        {
            "entries": valid_entry.fillna(False),
            "sl_pct": sl_pct.fillna(0.0),
            "tp_pct": tp_pct.fillna(0.0),
            "entry_ref": entry_ref,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "sl": stop_loss,
            "tp": take_profit,
            "invalidation_level": range_high,
        },
        index=df.index,
    )


@dataclass
class SimulatedEntry:
    """Cumple `paper_ledger.PaperEntryLike` (evaluate_exit) Y
    `paper_ledger.PendingEntryLike` (evaluate_pending_fill) — permite
    reutilizar AMBAS funciones (el mismo código que usa el paper ledger en
    vivo) sin depender del ORM `TradeEntry`."""

    entry_time: datetime
    entry_price: Decimal
    sl: Decimal
    tp: Decimal
    invalidation_level: Decimal | None
    horizon_class: str | None
    entry_zone_high: Decimal | None


def _row_to_candle(row: "pd.Series[object]", open_time: datetime, asset: str, timeframe: str) -> Candle:
    return Candle(
        asset=asset,
        timeframe=timeframe,  # type: ignore[arg-type]
        open_time=open_time,
        close_time=row["close_time"],
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
        quote_volume=Decimal(str(row["quote_volume"])),
    )


def simulate_trades(
    df: pd.DataFrame,
    lookback: int,
    volume_confirm_mult: float,
    settings: Settings,
    asset: str,
    timeframe: Literal["1h", "4h"],
    slippage: float = DEFAULT_SLIPPAGE,
) -> pd.DataFrame:
    """Sustituye a `run_portfolio`/`vectorbt`. `df` debe tener índice
    datetime ascendente (open_time) y columnas open/high/low/close/volume/
    quote_volume/close_time. No acumula posiciones (una entrada a la vez,
    igual que el sistema en vivo evalúa un activo — sección 9.2).

    Por cada señal: 1) intenta el FILL de una orden límite simulada dentro
    de `settings.entry_ttl_minutes` contra `paper_ledger.evaluate_pending_fill`
    (bug #11) — sin fill, la señal se descarta (equivale a `expired`, sin
    trade). 2) Con fill, acota las velas de salida a
    `(entry_time_real, entry_time_real + horizonte]` (el horizonte cuenta
    desde el FILL, no desde la señal) y llama a `paper_ledger.evaluate_exit`
    — mismo código en vivo y en backtest. Si el horizonte cae después de la
    última vela disponible, el trade queda sin resolver (censura por límite
    de datos) y se descarta — evita contar como cerrado un trade que en
    realidad seguiría abierto."""
    if len(df) == 0:
        return pd.DataFrame(columns=TRADES_COLUMNS)

    signals = generate_signals(df, lookback, volume_confirm_mult)
    horizon_class = horizon_for_timeframe(timeframe).value
    slippage_d = Decimal(str(slippage))
    ttl = timedelta(minutes=settings.entry_ttl_minutes)

    entries_arr = signals["entries"].to_numpy()
    n = len(df)
    last_close_time = df["close_time"].iloc[-1]

    rows: list[dict[str, object]] = []
    i = 0
    while i < n:
        if not entries_arr[i]:
            i += 1
            continue

        signal_time = df["close_time"].iloc[i]
        entry_zone_high = Decimal(str(signals["entry_high"].iloc[i]))
        sl = Decimal(str(signals["sl"].iloc[i]))
        tp = Decimal(str(signals["tp"].iloc[i]))
        invalidation_level = Decimal(str(signals["invalidation_level"].iloc[i]))

        # FIX (2026-07-07): mismo criterio que `paper_ledger.evaluate_pending_fill`
        # (ver su DECISION) — se filtra por `open_time` (no `close_time`),
        # incluyendo la vela INMEDIATAMENTE siguiente a la señal
        # (open_time == signal_time). `entry_ttl_minutes` (45 por defecto)
        # es menor que la duración de cualquier timeframe operado (1h/4h):
        # exigir el cierre de la vela haría que la orden expirase siempre.
        fill_deadline = signal_time + ttl
        pending_window = df[(df.index >= signal_time) & (df.index <= fill_deadline)]
        pending_candles = [_row_to_candle(row, open_time, asset, timeframe) for open_time, row in pending_window.iterrows()]

        pending_entry = SimulatedEntry(
            entry_time=signal_time, entry_price=entry_zone_high, sl=sl, tp=tp,
            invalidation_level=invalidation_level, horizon_class=horizon_class,
            entry_zone_high=entry_zone_high,
        )
        fill = evaluate_pending_fill(pending_entry, pending_candles, settings, fill_deadline)
        if fill is None:
            # DECISION (bug #11): sin pullback a la zona dentro del TTL, la
            # orden habría expirado — no hay trade. Se salta el resto de la
            # ventana de TTL antes de reanudar el escaneo (evita reintentar
            # la MISMA ruptura sostenida vela a vela mientras el precio
            # sigue por encima de la zona; análogo al dedupe por vela de
            # señal del bug #10 en vivo, que aquí no existe como estado
            # persistido).
            later = df.index[df.index > fill_deadline]
            i = df.index.get_loc(later[0]) if len(later) else n
            continue

        entry_price = fill.fill_price
        entry_time = fill.fill_time
        sl_pct_at_entry = float((entry_price - sl) / entry_price) if entry_price > 0 else 0.0

        deadline = entry_time + max_hold_for_horizon(settings, horizon_class)
        if deadline > last_close_time:
            # No hay suficiente historia futura para saber si habría cerrado -> se descarta.
            later = df.index[df.index > fill_deadline]
            i = df.index.get_loc(later[0]) if len(later) else n
            continue

        sim_entry = SimulatedEntry(
            entry_time=entry_time, entry_price=entry_price, sl=sl, tp=tp,
            invalidation_level=invalidation_level, horizon_class=horizon_class,
            entry_zone_high=entry_zone_high,
        )

        decision: ExitDecision | None
        if fill.candle_low <= sl:
            # Mismo criterio conservador que `paper_ledger._process_pending_entry`:
            # fill y SL en la MISMA vela -> pérdida completa inmediata.
            decision = ExitDecision(entry_time, sl, "closed_sl")
        else:
            window_df = df[(df.index > entry_time) & (df["close_time"] <= deadline)]
            candles = [_row_to_candle(row, open_time, asset, timeframe) for open_time, row in window_df.iterrows()]
            decision = evaluate_exit(sim_entry, candles, settings, deadline)
            if decision is None:
                i += 1
                continue

        entry_price_adj = entry_price * (1 + slippage_d)
        exit_price_adj = decision.exit_price * (1 - slippage_d)
        _, _, pnl_pct_net = compute_trade_pnl(entry_price_adj, exit_price_adj, Decimal("1"), settings.taker_fee)

        rows.append(
            {
                "Entry Timestamp": entry_time,
                "Exit Timestamp": decision.exit_time,
                "exit_type": decision.exit_type,
                "Avg Entry Price": float(entry_price),
                "Avg Exit Price": float(decision.exit_price),
                "Return": float(pnl_pct_net),
                "sl_pct_at_entry": sl_pct_at_entry,
            }
        )

        later = df.index[df.index > decision.exit_time]
        if len(later) == 0:
            break
        i = df.index.get_loc(later[0])

    return pd.DataFrame(rows, columns=TRADES_COLUMNS)
