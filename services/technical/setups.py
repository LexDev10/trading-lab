"""Detección de ruptura de rango con confirmación de volumen — la hipótesis
de edge de la sección 3.1. Único `setup_type` del MVP: `range_breakout`.

`compute_breakout_frame` es la MISMA función vectorizada que usa
`backtests/strategy_breakout.py` (regla crítica, sección 6): nunca
duplicar la lógica de señales entre scanner en vivo y backtest."""

import pandas as pd

from services.technical.indicators import atr, relative_volume, rolling_range

MIN_CANDLES_FOR_DETECTION = 21  # 20 de rolling_volume(20) + 1 vela actual


def compute_breakout_frame(df: pd.DataFrame, lookback: int, volume_confirm_mult: float) -> pd.DataFrame:
    """Vectorizado sobre toda la serie: para cada vela, ¿su cierre rompe el
    rango de las `lookback` velas anteriores con volumen >=
    `volume_confirm_mult` veces la media de 20? Solo largo (spot): solo se
    consideran rupturas al alza."""
    range_high, range_low = rolling_range(df["high"], df["low"], lookback)
    rel_vol = relative_volume(df["volume"], 20)
    atr_14 = atr(df["high"], df["low"], df["close"], 14)
    breakout = (df["close"] > range_high) & (rel_vol >= volume_confirm_mult)
    return pd.DataFrame(
        {
            "range_high": range_high,
            "range_low": range_low,
            "rel_volume": rel_vol,
            "atr_14": atr_14,
            "breakout": breakout.fillna(False),
        },
        index=df.index,
    )


def detect_range_breakout(
    df: pd.DataFrame, lookback: int, volume_confirm_mult: float
) -> dict | None:
    """`df` con columnas open_time/close_time/open/high/low/close/volume,
    ascendente, SOLO velas cerradas (ver `candles_to_frame`).

    Devuelve un dict con la evidencia de la ruptura de la última vela
    cerrada, o `None` si no hay ruptura confirmada."""
    min_len = max(lookback, 20) + 1
    if len(df) < min_len:
        return None

    frame = compute_breakout_frame(df, lookback, volume_confirm_mult)
    last = frame.iloc[-1]

    if pd.isna(last["range_high"]) or pd.isna(last["rel_volume"]) or pd.isna(last["atr_14"]):
        return None
    if not bool(last["breakout"]):
        return None

    return {
        "close": float(df["close"].iloc[-1]),
        "range_high": float(last["range_high"]),
        "range_low": float(last["range_low"]),
        "rel_volume": float(last["rel_volume"]),
        "atr_14": float(last["atr_14"]),
        "open_time": df["open_time"].iloc[-1],
        "close_time": df["close_time"].iloc[-1],
    }
