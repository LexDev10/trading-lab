"""Backtest de regresión (sección 18): un fixture FIJO de velas debe
producir métricas EXACTAS y conocidas. Protege contra divergencia
silenciosa entre la lógica de señales en vivo (services/technical/) y el
backtest (backtests/) — si alguien cambia una fórmula en un lado y no en
el otro, este test lo detecta.

# FIX (2026-07-06, bug #4 del CHANGELOG): `simulate_trades` reemplaza al
# antiguo `run_portfolio` (vectorbt, solo SL/TP). Los fixtures de
# invalidación/tiempo prueban que el motor nuevo reutiliza de verdad
# `paper_ledger.evaluate_exit` — no solo SL/TP como antes."""

import pandas as pd
import pytest

from app.config import Settings
from backtests.strategy_breakout import generate_signals, simulate_trades


def _settings() -> Settings:
    return Settings(_env_file=None)


def _base_fixture(n: int = 125) -> pd.DataFrame:
    """Vela plana (rango 99-101, cierre 100, volumen 1000) durante 70
    velas (calienta `range_high`=101/`range_low`=99/`atr_14`≈2.0), seguida
    de una ruptura limpia en la vela 70 confirmada por volumen (3x la
    media): `high=116, low=100, close=115, volume=3000`. `n=125` deja
    margen suficiente para que el horizonte de 48h (1h intraday, sección
    10.1) quede siempre dentro del histórico disponible — si no,
    `simulate_trades` descarta el trade por datos insuficientes (censura
    documentada, ver docstring de la función)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    high = [101.0] * n
    low = [99.0] * n
    close = [100.0] * n
    volume = [1000.0] * n

    high[70] = 116.0
    low[70] = 100.0
    close[70] = 115.0
    volume[70] = 3000.0

    df = pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df["open"] = df["close"]
    df["quote_volume"] = df["volume"] * df["close"]
    df["close_time"] = df.index + pd.Timedelta(hours=1)
    return df


def test_generate_signals_produces_known_entry():
    df = _base_fixture()
    signals = generate_signals(df, lookback=20, volume_confirm_mult=1.5)

    entries = signals[signals["entries"]]
    assert len(entries) == 1
    assert entries.index[0] == pd.Timestamp("2024-01-03 22:00:00", tz="UTC")
    assert entries["sl_pct"].iloc[0] == pytest.approx(0.09722222, rel=1e-6)
    assert entries["tp_pct"].iloc[0] == pytest.approx(0.19444444, rel=1e-6)
    # Precios absolutos que usa `simulate_trades` para construir la
    # entrada simulada — misma fórmula que `signal_builder.build_technical_signal`.
    assert entries["entry_ref"].iloc[0] == pytest.approx(108.0, rel=1e-6)
    assert entries["sl"].iloc[0] == pytest.approx(97.5, rel=1e-6)
    assert entries["tp"].iloc[0] == pytest.approx(129.0, rel=1e-6)
    assert entries["invalidation_level"].iloc[0] == pytest.approx(101.0, rel=1e-6)


def test_simulate_trades_hits_take_profit_when_no_earlier_exit():
    """Tras la ruptura, sube de forma lineal y estable hasta tocar TP sin
    que la invalidación ni el SL disparen antes — mismo camino "limpio"
    del backtest original, ahora con el motor de salidas realista."""
    df = _base_fixture()
    for i in range(71, 94):
        df.loc[df.index[i], "close"] = 115.0 + (i - 70) * 1.0
    for i in range(71, 94):
        df.loc[df.index[i], "high"] = df["close"].iloc[i] + 1.0
        df.loc[df.index[i], "low"] = df["close"].iloc[i] - 1.0
    for i in range(94, len(df)):
        df.loc[df.index[i], ["close", "high", "low"]] = [
            df["close"].iloc[93],
            df["close"].iloc[93] + 1.0,
            df["close"].iloc[93] - 1.0,
        ]

    settings = _settings()
    trades = simulate_trades(df, lookback=20, volume_confirm_mult=1.5, settings=settings, asset="TESTUSDT", timeframe="1h")

    assert len(trades) == 1
    trade = trades.iloc[0]
    # Entry Timestamp = close_time de la vela de ruptura (vela 70), no su
    # open_time: mismo criterio que `entry.entry_time` en el paper ledger
    # (la vela de la propia entrada queda excluida del seguimiento).
    assert trade["Entry Timestamp"] == pd.Timestamp("2024-01-03 23:00:00", tz="UTC")
    assert trade["Exit Timestamp"] == pd.Timestamp("2024-01-04 12:00:00", tz="UTC")
    assert trade["exit_type"] == "closed_tp"
    assert trade["Avg Entry Price"] == pytest.approx(108.0, rel=1e-6)
    assert trade["Avg Exit Price"] == pytest.approx(129.0, rel=1e-6)
    assert trade["sl_pct_at_entry"] == pytest.approx(0.09722222, rel=1e-6)
    assert trade["Return"] == pytest.approx(0.191773, rel=1e-4)


def test_simulate_trades_hits_invalidation_before_sl_or_tp():
    """Tras la ruptura, el precio se mantiene por encima de
    `invalidation_level` unas velas y luego cierra por debajo — sin tocar
    ni el SL ni el TP. Prueba que `simulate_trades` reutiliza de verdad
    `evaluate_exit` (bug #4): el viejo motor basado solo en `vectorbt`
    SL/TP nunca habría cerrado este trade."""
    df = _base_fixture()
    for i in range(71, 76):
        df.loc[df.index[i], ["close", "high", "low"]] = [107.0, 110.0, 104.0]
    df.loc[df.index[76], ["close", "high", "low"]] = [98.5, 102.0, 98.0]
    for i in range(77, len(df)):
        df.loc[df.index[i], ["close", "high", "low"]] = [98.5, 102.0, 98.0]

    settings = _settings()
    trades = simulate_trades(df, lookback=20, volume_confirm_mult=1.5, settings=settings, asset="TESTUSDT", timeframe="1h")

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["Entry Timestamp"] == pd.Timestamp("2024-01-03 23:00:00", tz="UTC")
    assert trade["Exit Timestamp"] == pd.Timestamp("2024-01-04 05:00:00", tz="UTC")
    assert trade["exit_type"] == "closed_invalidated"
    assert trade["Avg Exit Price"] == pytest.approx(98.5, rel=1e-6)
    assert trade["Return"] == pytest.approx(-0.090239, rel=1e-4)
    assert trade["Return"] < 0


def test_simulate_trades_discards_trade_censored_by_end_of_data():
    """Si el histórico termina antes del horizonte máximo (48h para 1h)
    SIN que SL/TP/invalidación hayan disparado antes, el trade queda sin
    resolver y se descarta — no se cuenta como cerrado (evita inflar/
    deflactar la expectancy con un trade todavía abierto, limitación
    conocida #9 del walk-forward). El precio se mantiene en zona "segura"
    (por encima de `invalidation_level`, sin tocar SL ni TP) durante todo
    el histórico disponible, que termina en la vela 89 — mucho antes del
    deadline (vela 119, `entry_time` + 48h)."""
    df = _base_fixture(n=90)
    for i in range(71, len(df)):
        df.loc[df.index[i], ["close", "high", "low"]] = [107.0, 110.0, 104.0]

    settings = _settings()
    trades = simulate_trades(df, lookback=20, volume_confirm_mult=1.5, settings=settings, asset="TESTUSDT", timeframe="1h")
    assert len(trades) == 0


def test_no_breakout_no_trades():
    """Fixture plano (sin ruptura): cero señales, cero trades."""
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n, "volume": [1000.0] * n},
        index=idx,
    )
    df["open"] = df["close"]
    df["quote_volume"] = df["volume"] * df["close"]
    df["close_time"] = df.index + pd.Timedelta(hours=1)

    settings = _settings()
    trades = simulate_trades(df, lookback=20, volume_confirm_mult=1.5, settings=settings, asset="TESTUSDT", timeframe="1h")
    assert len(trades) == 0
