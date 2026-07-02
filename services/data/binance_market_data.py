"""Cliente de datos de mercado de Binance (solo lectura).

# DECISION: los datos de mercado para señales se toman SIEMPRE de la API de
# producción, independientemente de ENVIRONMENT (testnet/live). Solo la
# ejecución de órdenes (fase 1+) usa la URL de testnet. Ver sección 10.1
# del documento de especificación.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from binance.spot import Spot

from core.schemas.market import Candle, MarketSnapshot

MARKET_DATA_BASE_URL = "https://api.binance.com"

Interval = Literal["1h", "4h"]


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


class BinanceMarketData:
    def __init__(self) -> None:
        self._client = Spot(base_url=MARKET_DATA_BASE_URL)

    def fetch_klines(self, asset: str, timeframe: Interval, limit: int = 500) -> list[Candle]:
        raw = self._client.klines(symbol=asset, interval=timeframe, limit=limit)
        candles = []
        for row in raw:
            (
                open_time_ms,
                open_,
                high,
                low,
                close,
                volume,
                close_time_ms,
                quote_volume,
                *_rest,
            ) = row
            candles.append(
                Candle(
                    asset=asset,
                    timeframe=timeframe,
                    open_time=_ms_to_dt(open_time_ms),
                    close_time=_ms_to_dt(close_time_ms),
                    open=Decimal(str(open_)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=Decimal(str(volume)),
                    quote_volume=Decimal(str(quote_volume)),
                )
            )
        return candles

    def fetch_ticker_24h(self, assets: list[str]) -> list[MarketSnapshot]:
        raw = self._client.ticker_24hr(symbols=assets)
        now = datetime.now(tz=UTC)
        snapshots = []
        for item in raw:
            bid = Decimal(str(item["bidPrice"]))
            ask = Decimal(str(item["askPrice"]))
            mid = (bid + ask) / 2
            spread_bps = ((ask - bid) / mid) * Decimal("10000") if mid > 0 else Decimal("0")
            snapshots.append(
                MarketSnapshot(
                    asset=item["symbol"],
                    ts=now,
                    bid=bid,
                    ask=ask,
                    spread_bps=spread_bps,
                    quote_vol_24h=Decimal(str(item["quoteVolume"])),
                    change_24h_pct=Decimal(str(item["priceChangePercent"])),
                    raw=item,
                )
            )
        return snapshots
