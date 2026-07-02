import pandas as pd

from core.enums import Regime
from services.scanner.regime import blocks_new_entries, compute_btc_regime


def _trend_df(n: int, step: float) -> pd.DataFrame:
    closes = [100 + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
        }
    )


def test_trend_up_detected_and_blocks_are_false():
    df = _trend_df(250, step=0.5)
    regime, details = compute_btc_regime(df)
    assert regime == Regime.trend_up
    assert blocks_new_entries(regime) is False


def test_trend_down_detected_and_blocks_entries():
    df = _trend_df(250, step=-0.5)
    regime, details = compute_btc_regime(df)
    assert regime == Regime.trend_down
    assert blocks_new_entries(regime) is True


def test_chop_high_vol_detected_and_blocks_entries():
    n = 250
    closes = [100.0] * n
    high = [101.0] * n
    low = [99.0] * n
    # última vela: rango de volatilidad extremo frente al histórico plano.
    high[-1] = 150.0
    low[-1] = 50.0
    df = pd.DataFrame({"high": high, "low": low, "close": closes})

    regime, details = compute_btc_regime(df)

    assert regime == Regime.chop_high_vol
    assert blocks_new_entries(regime) is True


def test_insufficient_history_returns_range_and_does_not_block():
    df = _trend_df(50, step=0.5)
    regime, details = compute_btc_regime(df)
    assert regime == Regime.range
    assert details["reason"] == "insufficient_history"
    assert blocks_new_entries(regime) is False
