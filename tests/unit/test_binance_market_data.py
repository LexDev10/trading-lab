"""Verifica el parseo de respuestas de Binance a los contratos Pydantic,
usando fixtures grabadas (sin red real). Ver sección 18 del documento."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from binance.error import ClientError

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


def test_symbol_exists_returns_true_when_found(monkeypatch):
    client = BinanceMarketData()
    monkeypatch.setattr(client._client, "exchange_info", lambda **kwargs: {"symbols": [{"symbol": "BTCUSDT"}]})

    assert client.symbol_exists("BTCUSDT") is True


def test_symbol_exists_returns_false_for_invalid_symbol_error(monkeypatch):
    """FIX (2026-07-07): Binance no devuelve una lista vacía para un
    símbolo inexistente, responde HTTP 400 / error_code=-1121."""
    client = BinanceMarketData()

    def _raise(**kwargs):
        raise ClientError(400, -1121, "Invalid symbol.", {})

    monkeypatch.setattr(client._client, "exchange_info", _raise)

    assert client.symbol_exists("NOEXISTEUSDT") is False


def test_symbol_exists_reraises_other_client_errors(monkeypatch):
    client = BinanceMarketData()

    def _raise(**kwargs):
        raise ClientError(418, -1003, "Too many requests.", {})

    monkeypatch.setattr(client._client, "exchange_info", _raise)

    with pytest.raises(ClientError):
        client.symbol_exists("BTCUSDT")
