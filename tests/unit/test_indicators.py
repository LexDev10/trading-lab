import pandas as pd

from services.technical.indicators import atr, ema, relative_volume, rolling_range, rsi


def test_ema_of_constant_series_equals_constant():
    series = pd.Series([50.0] * 30)
    result = ema(series, 10)
    assert (result.round(6) == 50.0).all()


def test_ema_reacts_to_trend():
    series = pd.Series(list(range(1, 31)), dtype=float)
    result = ema(series, 5)
    assert result.iloc[-1] > result.iloc[0]


def test_atr_is_zero_when_no_range_and_no_gaps():
    n = 20
    high = pd.Series([100.0] * n)
    low = pd.Series([100.0] * n)
    close = pd.Series([100.0] * n)
    result = atr(high, low, close, length=14)
    assert (result.round(8) == 0).all()


def test_atr_is_positive_with_real_range():
    high = pd.Series([101.0, 102.0, 103.0, 104.0, 105.0])
    low = pd.Series([99.0, 100.0, 101.0, 102.0, 103.0])
    close = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0])
    result = atr(high, low, close, length=3)
    assert (result > 0).all()


def test_rsi_is_100_for_strictly_increasing_series():
    series = pd.Series(list(range(1, 31)), dtype=float)
    result = rsi(series, length=14)
    assert result.iloc[-1] == 100.0


def test_rsi_is_bounded_0_100():
    series = pd.Series([50, 52, 48, 55, 51, 60, 45, 47, 53, 58], dtype=float)
    result = rsi(series, length=5)
    assert (result.dropna() >= 0).all() and (result.dropna() <= 100).all()


def test_relative_volume_is_one_for_constant_volume():
    volume = pd.Series([1000.0] * 25)
    result = relative_volume(volume, length=20)
    assert (result.dropna().round(6) == 1.0).all()


def test_rolling_range_excludes_current_row():
    # Un pico enorme en la última vela NO debe aparecer en su propio
    # range_high/range_low (anti look-ahead).
    high = pd.Series([10.0] * 25 + [9999.0])
    low = pd.Series([5.0] * 25 + [0.01])
    range_high, range_low = rolling_range(high, low, lookback=20)
    assert range_high.iloc[-1] == 10.0
    assert range_low.iloc[-1] == 5.0
