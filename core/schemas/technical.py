"""Contrato de la señal técnica. Ver sección 7.2 de
ESPECIFICACION_SISTEMA_TRADING.md.

Regla de construcción: stop_loss = mínimo del rango − 0.5×ATR14.
take_profit tal que R:R bruto ≥ 2.0 (el neto lo verifica el risk engine).
"""

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

from core.enums import HorizonClass, Regime, SignalDirection, TechnicalConviction


class TechnicalSignal(BaseModel):
    asset: str
    generated_at: AwareDatetime
    candle_close_time: AwareDatetime
    timeframe: Literal["1h", "4h"]
    regime: Regime
    direction: SignalDirection
    conviction: TechnicalConviction
    setup_type: Literal["range_breakout"]
    entry_zone: tuple[Decimal, Decimal]
    stop_loss: Decimal
    take_profit: Decimal
    atr_14: Decimal
    rel_volume: Decimal
    horizon_class: HorizonClass
    invalidation_rule: str
    invalidation_level: Decimal
    evidence: dict[str, Any]
