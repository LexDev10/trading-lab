"""Construye `TechnicalSignal` a partir de la detección de ruptura. Aquí
(y solo aquí) se convierte a Decimal — ver DECISION en indicators.py.

# DECISION: la clasificación de `conviction` (strong/moderate/weak) no
# tiene fórmula exacta en el documento ("los valores exactos se calibran
# con datos de fase 1-2", sección 13). Regla conservadora explícita:
#   strong   -> rel_volume >= 2 × VOLUME_CONFIRM_MULT
#   moderate -> rel_volume >= VOLUME_CONFIRM_MULT (umbral mínimo de detección)
#   (no existe "weak" para setup_type=range_breakout en el MVP: si no
#   alcanza VOLUME_CONFIRM_MULT, detect_range_breakout ya descarta el setup)
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

import pandas as pd

from app.config import Settings
from core.enums import HorizonClass, Regime, SignalDirection, TechnicalConviction
from core.schemas.technical import TechnicalSignal
from services.technical.setups import detect_range_breakout

GROSS_RR_TARGET = Decimal("2.0")
STOP_ATR_BUFFER = Decimal("0.5")


def _classify_conviction(rel_volume: Decimal, volume_confirm_mult: Decimal) -> TechnicalConviction:
    if rel_volume >= volume_confirm_mult * Decimal("2"):
        return TechnicalConviction.strong
    return TechnicalConviction.moderate


def horizon_for_timeframe(timeframe: Literal["1h", "4h"]) -> HorizonClass:
    """Único mapeo timeframe -> horizonte (sección 7.2). Reutilizado por
    `backtests/strategy_breakout.py` para construir entradas simuladas con
    el mismo `horizon_class` que produciría `build_technical_signal` en
    vivo (regla crítica, sección 6)."""
    return HorizonClass.hours if timeframe == "1h" else HorizonClass.days


def build_technical_signal(
    asset: str,
    timeframe: Literal["1h", "4h"],
    df: pd.DataFrame,
    regime: Regime,
    settings: Settings,
    generated_at: datetime,
) -> TechnicalSignal | None:
    detection = detect_range_breakout(
        df,
        lookback=settings.range_lookback_candles,
        volume_confirm_mult=float(settings.volume_confirm_mult),
    )
    if detection is None:
        return None

    close = Decimal(str(detection["close"]))
    range_high = Decimal(str(detection["range_high"]))
    range_low = Decimal(str(detection["range_low"]))
    atr_14 = Decimal(str(detection["atr_14"]))
    rel_volume = Decimal(str(detection["rel_volume"]))

    entry_low = min(range_high, close)
    entry_high = max(range_high, close)
    entry_ref = (entry_low + entry_high) / Decimal("2")

    stop_loss = range_low - (STOP_ATR_BUFFER * atr_14)
    risk_distance = entry_ref - stop_loss
    if risk_distance <= 0:
        # Fail-closed: rango degenerado / ATR inconsistente -> no hay setup.
        return None

    take_profit = entry_ref + (GROSS_RR_TARGET * risk_distance)

    return TechnicalSignal(
        asset=asset,
        generated_at=generated_at,
        candle_close_time=detection["close_time"],
        timeframe=timeframe,
        regime=regime,
        direction=SignalDirection.long,
        conviction=_classify_conviction(rel_volume, settings.volume_confirm_mult),
        setup_type="range_breakout",
        entry_zone=(entry_low, entry_high),
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr_14=atr_14,
        rel_volume=rel_volume,
        horizon_class=horizon_for_timeframe(timeframe),
        invalidation_rule=f"close_{timeframe}_below_{range_high}",
        invalidation_level=range_high,
        evidence={
            "range_high": str(range_high),
            "range_low": str(range_low),
            "breakout_close": str(close),
            "lookback_candles": settings.range_lookback_candles,
            "volume_confirm_mult": str(settings.volume_confirm_mult),
        },
    )
