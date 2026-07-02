"""Sección 18: test que verifica que ninguna señal usa la vela en curso."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.schemas.market import Candle
from services.technical.indicators import candles_to_frame


def _candle(open_time: datetime, close_time: datetime) -> Candle:
    return Candle(
        asset="BTCUSDT",
        timeframe="1h",
        open_time=open_time,
        close_time=close_time,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
    )


def test_in_progress_candle_is_dropped():
    now = datetime.now(tz=UTC)
    closed = _candle(now - timedelta(hours=2), now - timedelta(hours=1))
    in_progress = _candle(now - timedelta(minutes=30), now + timedelta(minutes=30))

    df = candles_to_frame([closed, in_progress], now=now)

    assert len(df) == 1
    assert df.iloc[0]["close"] == 100.5


def test_all_closed_candles_are_kept():
    now = datetime.now(tz=UTC)
    candles = [
        _candle(now - timedelta(hours=3), now - timedelta(hours=2)),
        _candle(now - timedelta(hours=2), now - timedelta(hours=1)),
    ]
    df = candles_to_frame(candles, now=now)
    assert len(df) == 2
