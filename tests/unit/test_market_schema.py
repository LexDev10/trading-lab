from decimal import Decimal

from core.schemas.market import Candle, MarketSnapshot


def test_candle_uses_decimal_not_float():
    candle = Candle(
        asset="BTCUSDT",
        timeframe="1h",
        open_time="2024-01-01T00:00:00Z",
        close_time="2024-01-01T00:59:59Z",
        open="0.01634790",
        high="0.80000000",
        low="0.01575800",
        close="0.01577100",
        volume="148976.11427815",
        quote_volume="2434.19055334",
    )
    assert isinstance(candle.open, Decimal)
    assert isinstance(candle.close, Decimal)
    assert candle.open == Decimal("0.01634790")


def test_market_snapshot_spread_fields_are_decimal():
    snapshot = MarketSnapshot(
        asset="BTCUSDT",
        ts="2024-01-01T00:00:00Z",
        bid="60000.00",
        ask="60006.00",
        spread_bps="1.0",
        quote_vol_24h="534798000",
        change_24h_pct="-1.96",
        raw={"symbol": "BTCUSDT"},
    )
    assert isinstance(snapshot.spread_bps, Decimal)
    assert snapshot.change_24h_pct == Decimal("-1.96")
