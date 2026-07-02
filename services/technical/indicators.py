"""Indicadores calculados en Python (pandas/numpy), nunca por un LLM — ver
principio de diseño 1 del documento de especificación.

# DECISION: los cálculos internos de indicadores se hacen en float (pandas
# estándar), como es habitual en análisis estadístico de series. La
# conversión a Decimal ocurre al construir el `TechnicalSignal` final
# (services/technical/signal_builder.py), que es donde el documento exige
# Decimal (precios/cantidades que pueden derivar en órdenes reales).

Todas las funciones asumen velas ya cerradas y ordenadas ascendentemente
por `open_time`. Ninguna función debe recibir ni usar la vela en curso
(anti look-ahead, sección 18 / Apéndice B).
"""

from datetime import UTC, datetime

import pandas as pd

from core.schemas.market import Candle


def candles_to_frame(candles: list[Candle], now: datetime | None = None) -> pd.DataFrame:
    """Convierte velas Pydantic a un DataFrame ascendente por open_time,
    descartando cualquier vela cuyo close_time sea posterior a `now`
    (anti look-ahead: nunca usar la vela en curso)."""
    now = now or datetime.now(tz=UTC)
    rows = [
        {
            "open_time": c.open_time,
            "close_time": c.close_time,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "quote_volume": float(c.quote_volume),
        }
        for c in candles
        if c.close_time <= now
    ]
    df = pd.DataFrame(rows).sort_values("open_time").reset_index(drop=True)
    return df


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    return result.fillna(100)  # avg_loss == 0 -> fuerza alcista máxima


def relative_volume(volume: pd.Series, length: int = 20) -> pd.Series:
    avg_volume = volume.rolling(window=length).mean()
    return volume / avg_volume


def rolling_range(
    high: pd.Series, low: pd.Series, lookback: int
) -> tuple[pd.Series, pd.Series]:
    """Máximo/mínimo del rango de las `lookback` velas ANTERIORES a cada
    fila (excluye la vela actual, evita look-ahead sobre sí misma)."""
    range_high = high.shift(1).rolling(window=lookback).max()
    range_low = low.shift(1).rolling(window=lookback).min()
    return range_high, range_low
