"""Régimen de BTC en 4h — filtro de cartera crítico (sección 8.3). Spot es
solo largo: si BTC no acompaña, el sistema entero no abre posiciones."""

import pandas as pd

from core.enums import Regime
from services.technical.indicators import atr, ema

EMA_FAST = 50
EMA_SLOW = 200
ATR_LENGTH = 14
CHOP_ATR_PCT_PERCENTILE = 90

# Regímenes en los que el sistema entero no abre posiciones nuevas.
BLOCKING_REGIMES = {Regime.trend_down, Regime.chop_high_vol}


def compute_btc_regime(candles_4h: pd.DataFrame) -> tuple[Regime, dict]:
    """`candles_4h` debe tener columnas high/low/close, solo velas cerradas,
    ordenadas ascendentemente por open_time. Devuelve el régimen y un dict
    de detalles para `regime_log`."""
    if len(candles_4h) < EMA_SLOW + 2:
        # Fail-closed: sin histórico suficiente para EMA200, no se puede
        # clasificar con confianza -> tratar como range (no bloquea, pero
        # tampoco confirma tendencia; el resto de filtros duros seguirán
        # aplicando por separado).
        return Regime.range, {"reason": "insufficient_history", "candles": len(candles_4h)}

    close = candles_4h["close"]
    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    atr_14 = atr(candles_4h["high"], candles_4h["low"], close, ATR_LENGTH)
    atr_pct = (atr_14 / close) * 100

    price = float(close.iloc[-1])
    ema_fast_last = float(ema_fast.iloc[-1])
    ema_fast_prev = float(ema_fast.iloc[-2])
    ema_slow_last = float(ema_slow.iloc[-1])
    atr_pct_last = float(atr_pct.iloc[-1])
    atr_pct_p90 = float(atr_pct.dropna().quantile(CHOP_ATR_PCT_PERCENTILE / 100))

    slope_up = ema_fast_last > ema_fast_prev
    slope_down = ema_fast_last < ema_fast_prev

    details = {
        "price": price,
        "ema50": ema_fast_last,
        "ema200": ema_slow_last,
        "atr_pct": atr_pct_last,
        "atr_pct_p90_historic": atr_pct_p90,
        "ema50_slope_up": slope_up,
    }

    if atr_pct_last > atr_pct_p90:
        return Regime.chop_high_vol, details
    if price > ema_fast_last > ema_slow_last and slope_up:
        return Regime.trend_up, details
    if price < ema_fast_last < ema_slow_last and slope_down:
        return Regime.trend_down, details
    return Regime.range, details


def blocks_new_entries(regime: Regime) -> bool:
    return regime in BLOCKING_REGIMES
