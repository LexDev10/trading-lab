import pandas as pd

from services.technical.setups import detect_range_breakout


def _flat_range_df(n: int, range_high: float = 110.0, range_low: float = 90.0, volume: float = 1000.0):
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
            "close_time": pd.date_range("2024-01-01 00:59", periods=n, freq="1h", tz="UTC"),
            "open": [100.0] * n,
            "high": [range_high] * n,
            "low": [range_low] * n,
            "close": [100.0] * n,
            "volume": [volume] * n,
        }
    )


def test_no_breakout_when_price_stays_in_range():
    df = _flat_range_df(30)
    result = detect_range_breakout(df, lookback=20, volume_confirm_mult=1.5)
    assert result is None


def test_breakout_detected_with_close_above_range_and_volume_confirmation():
    df = _flat_range_df(30)
    df.loc[df.index[-1], "close"] = 115.0
    df.loc[df.index[-1], "high"] = 116.0
    df.loc[df.index[-1], "volume"] = 3000.0  # 3x la media -> confirma

    result = detect_range_breakout(df, lookback=20, volume_confirm_mult=1.5)

    assert result is not None
    assert result["close"] == 115.0
    assert result["range_high"] == 110.0


def test_breakout_rejected_without_volume_confirmation():
    df = _flat_range_df(30)
    df.loc[df.index[-1], "close"] = 115.0
    df.loc[df.index[-1], "high"] = 116.0
    # volumen igual a la media -> rel_volume ~ 1.0, por debajo del umbral 1.5

    result = detect_range_breakout(df, lookback=20, volume_confirm_mult=1.5)

    assert result is None


def test_insufficient_history_returns_none():
    df = _flat_range_df(5)
    result = detect_range_breakout(df, lookback=20, volume_confirm_mult=1.5)
    assert result is None
