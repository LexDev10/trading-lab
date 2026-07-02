"""Señales vectorizadas de entrada + SL/TP para vectorbt, reutilizando LA
MISMA lógica que `services/technical/setups.py` y `signal_builder.py`
(regla crítica, sección 6: "la lógica de señales... debe ser el mismo
código importado, no dos implementaciones. Divergencia backtest/live es
un bug de primera clase.").

Solo se reimplementa aquí la aritmética de entry_ref/stop_loss/take_profit
en forma vectorizada (una fila por vela) en vez de "última fila" —
misma fórmula exacta que `signal_builder.build_technical_signal`, mismas
constantes importadas (no duplicadas)."""

import pandas as pd
import vectorbt as vbt

from services.technical.setups import compute_breakout_frame
from services.technical.signal_builder import GROSS_RR_TARGET, STOP_ATR_BUFFER

GROSS_RR_TARGET_F = float(GROSS_RR_TARGET)
STOP_ATR_BUFFER_F = float(STOP_ATR_BUFFER)

# Sección 14: fees 0.1% por lado + slippage pesimista (aproximado como 2bps
# uniformes; el tick size real varía por símbolo y no se modela aquí).
DEFAULT_FEES = 0.001
DEFAULT_SLIPPAGE = 0.0002


def generate_signals(df: pd.DataFrame, lookback: int, volume_confirm_mult: float) -> pd.DataFrame:
    """Devuelve un DataFrame alineado con `df` (mismo índice) con columnas
    `entries` (bool), `sl_pct`, `tp_pct` (fracción del precio de entrada,
    para `vectorbt.Portfolio.from_signals(sl_stop=..., tp_stop=...)`)."""
    frame = compute_breakout_frame(df, lookback, volume_confirm_mult)

    entry_ref = (frame["range_high"] + df["close"]) / 2
    stop_loss = frame["range_low"] - STOP_ATR_BUFFER_F * frame["atr_14"]
    risk_distance = entry_ref - stop_loss

    valid_entry = frame["breakout"] & (risk_distance > 0) & (entry_ref > 0)

    sl_pct = (risk_distance / entry_ref).where(valid_entry, other=0.0)
    tp_pct = (GROSS_RR_TARGET_F * risk_distance / entry_ref).where(valid_entry, other=0.0)

    return pd.DataFrame(
        {
            "entries": valid_entry.fillna(False),
            "sl_pct": sl_pct.fillna(0.0),
            "tp_pct": tp_pct.fillna(0.0),
        },
        index=df.index,
    )


def run_portfolio(
    df: pd.DataFrame,
    lookback: int,
    volume_confirm_mult: float,
    fees: float = DEFAULT_FEES,
    slippage: float = DEFAULT_SLIPPAGE,
) -> vbt.Portfolio:
    """`df` debe tener índice datetime (open_time) y columnas
    high/low/close/volume. No acumula posiciones (una entrada a la vez,
    comportamiento por defecto de vectorbt), igual que el sistema en vivo
    (sección 9.2: `max_positions` limita, pero por-activo es 1 a la vez en
    este MVP de backtest de un solo instrumento)."""
    signals = generate_signals(df, lookback, volume_confirm_mult)
    exits = pd.Series(False, index=df.index)
    return vbt.Portfolio.from_signals(
        close=df["close"],
        entries=signals["entries"],
        exits=exits,
        sl_stop=signals["sl_pct"].to_numpy(),
        tp_stop=signals["tp_pct"].to_numpy(),
        fees=fees,
        slippage=slippage,
    )
