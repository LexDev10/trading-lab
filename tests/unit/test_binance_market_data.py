"""Verifica el parseo de respuestas de Binance a los contratos Pydantic,
usando fixtures grabadas (sin red real). Ver sección 18 del documento."""

import json
from decimal import Decimal
from pathlib import Path

from services.data.binance_market_data import BinanceMarketData

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_fetch_klines_parses_fixture(monkeypatch):
    raw = json.loads((FIXTURES / "binance_klines_sample.json").read_text())
    client = BinanceMarketData()
    monkeypatch.setattr(client._client, "klines", lambda **kwargs: raw)

    candles = client.fetch_klines("BTCUSDT", "1h", limit=500)

    assert len(candles) == 2
    first = candles[0]
    assert first.asset == "BTCUSDT"
    assert first.timeframe == "1h"
    assert first.open == Decimal("0.01634790")
    assert first.close == Decimal("0.01577100")
    assert first.open_time < first.close_time


def test_fetch_ticker_24h_parses_fixture_and_computes_spread(monkeypatch):
    raw = json.loads((FIXTURES / "binance_ticker24h_sample.json").read_text())
    client = BinanceMarketData()
    monkeypatch.setattr(client._client, "ticker_24hr", lambda **kwargs: raw)

    snapshots = client.fetch_ticker_24h(["BTCUSDT"])

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.asset == "BTCUSDT"
    assert snap.bid == Decimal("60000.00000000")
    assert snap.ask == Decimal("60006.00000000")
    # spread_bps = (ask-bid)/mid * 10000
    assert snap.spread_bps > Decimal("0")
    assert snap.quote_vol_24h == Decimal("534798000.00000000")
