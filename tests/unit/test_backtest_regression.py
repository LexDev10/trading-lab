"""Backtest de regresión (sección 18): un fixture FIJO de velas debe
producir métricas EXACTAS y conocidas. Protege contra divergencia
silenciosa entre la lógica de señales en vivo (services/technical/) y el
backtest (backtests/) — si alguien cambia una fórmula en un lado y no en
el otro, este test lo detecta."""

import pandas as pd
import pytest

from backtests.strategy_breakout import generate_signals, run_portfolio


def _fixed_breakout_fixture() -> pd.DataFrame:
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    high = [101.0] * n
    low = [99.0] * n
    close = [100.0] * n
    volume = [1000.0] * n

    # Ruptura limpia en la vela 70, confirmada por volumen (3x la media).
    high[70] = 116.0
    low[70] = 100.0
    close[70] = 115.0
    volume[70] = 3000.0

    # Tras la ruptura: sube de forma lineal y estable hasta tocar TP.
    for i in range(71, n):
        close[i] = 115.0 + (i - 70) * 1.0
        high[i] = close[i] + 1.0
        low[i] = close[i] - 1.0

    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_generate_signals_produces_known_entry():
    df = _fixed_breakout_fixture()
    signals = generate_signals(df, lookback=20, volume_confirm_mult=1.5)

    entries = signals[signals["entries"]]
    assert len(entries) == 1
    assert entries.index[0] == pd.Timestamp("2024-01-03 22:00:00", tz="UTC")
    assert entries["sl_pct"].iloc[0] == pytest.approx(0.09722222, rel=1e-6)
    assert entries["tp_pct"].iloc[0] == pytest.approx(0.19444444, rel=1e-6)


def test_run_portfolio_produces_known_trade():
    df = _fixed_breakout_fixture()
    pf = run_portfolio(df, lookback=20, volume_confirm_mult=1.5)
    trades = pf.trades.records_readable

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["Entry Timestamp"] == pd.Timestamp("2024-01-03 22:00:00", tz="UTC")
    assert trade["Exit Timestamp"] == pd.Timestamp("2024-01-04 21:00:00", tz="UTC")
    assert trade["Avg Entry Price"] == pytest.approx(115.023, rel=1e-6)
    assert trade["Avg Exit Price"] == pytest.approx(138.0, rel=1e-6)
    assert trade["Return"] == pytest.approx(0.19756, rel=1e-4)
    assert pf.total_return() == pytest.approx(0.19736293, rel=1e-6)


def test_no_breakout_no_trades():
    """Fixture plano (sin ruptura): cero señales, cero trades."""
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n, "volume": [1000.0] * n},
        index=idx,
    )
    pf = run_portfolio(df, lookback=20, volume_confirm_mult=1.5)
    assert len(pf.trades.records_readable) == 0
    assert pf.total_return() == pytest.approx(0.0, abs=1e-9)
