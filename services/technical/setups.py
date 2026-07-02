"""Detección de ruptura de rango con confirmación de volumen — la hipótesis
de edge de la sección 3.1. Único `setup_type` del MVP: `range_breakout`.

Esta es la MISMA función que debe usar `backtests/strategy_breakout.py`
(regla crítica, sección 6): nunca duplicar la lógica de señales."""

import pandas as pd

from services.technical.indicators import atr, relative_volume, rolling_range

MIN_CANDLES_FOR_DETECTION = 21  # 20 de rolling_volume(20) + 1 vela actual


def detect_range_breakout(
    df: pd.DataFrame, lookback: int, volume_confirm_mult: float
) -> dict | None:
    """`df` con columnas open_time/close_time/open/high/low/close/volume,
    ascendente, SOLO velas cerradas (ver `candles_to_frame`).

    Devuelve un dict con la evidencia de la ruptura de la última vela
    cerrada, o `None` si no hay ruptura confirmada. Solo largo (spot):
    únicamente se detectan rupturas al alza."""
    min_len = max(lookback, 20) + 1
    if len(df) < min_len:
        return None

    range_high, range_low = rolling_range(df["high"], df["low"], lookback)
    rel_vol = relative_volume(df["volume"], 20)
    atr_14 = atr(df["high"], df["low"], df["close"], 14)

    last_close = df["close"].iloc[-1]
    last_range_high = range_high.iloc[-1]
    last_range_low = range_low.iloc[-1]
    last_rel_vol = rel_vol.iloc[-1]
    last_atr = atr_14.iloc[-1]

    if pd.isna(last_range_high) or pd.isna(last_rel_vol) or pd.isna(last_atr):
        return None
    if last_close <= last_range_high:
        return None
    if last_rel_vol < volume_confirm_mult:
        return None

    return {
        "close": float(last_close),
        "range_high": float(last_range_high),
        "range_low": float(last_range_low),
        "rel_volume": float(last_rel_vol),
        "atr_14": float(last_atr),
        "open_time": df["open_time"].iloc[-1],
        "close_time": df["close_time"].iloc[-1],
    }
