"""Contratos de datos de mercado crudos (velas y snapshots de ticker).
Ver sección 7 y 16 de ESPECIFICACION_SISTEMA_TRADING.md.

Regla del documento: precios y cantidades siempre Decimal, nunca float.
"""

from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel

Timeframe = Literal["1h", "4h"]


class Candle(BaseModel):
    asset: str
    timeframe: Timeframe
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal


class MarketSnapshot(BaseModel):
    asset: str
    ts: AwareDatetime
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    quote_vol_24h: Decimal
    change_24h_pct: Decimal
    raw: dict
