"""Cliente de datos de mercado de Binance (solo lectura).

# DECISION: los datos de mercado para señales se toman SIEMPRE de la API de
# producción, independientemente de ENVIRONMENT (testnet/live). Solo la
# ejecución de órdenes (fase 1+) usa la URL de testnet. Ver sección 10.1
# del documento de especificación.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from binance.error import ClientError
from binance.spot import Spot

from core.schemas.market import Candle, MarketSnapshot

MARKET_DATA_BASE_URL = "https://api.binance.com"

Interval = Literal["1h", "4h"]


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _parse_klines_rows(asset: str, timeframe: Interval, raw: list) -> list[Candle]:
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


class BinanceMarketData:
    def __init__(self) -> None:
        self._client = Spot(base_url=MARKET_DATA_BASE_URL)

    def fetch_klines(self, asset: str, timeframe: Interval, limit: int = 500) -> list[Candle]:
        raw = self._client.klines(symbol=asset, interval=timeframe, limit=limit)
        return _parse_klines_rows(asset, timeframe, raw)

    def fetch_klines_range(
        self, asset: str, timeframe: Interval, start_time: datetime, end_time: datetime
    ) -> list[Candle]:
        """Descarga paginada (max 1000 velas/llamada de Binance) para
        histórico de backtesting (sección 14: "2+ años de velas 1h/4h").
        Solo lectura, mismo endpoint público que `fetch_klines`."""
        all_candles: list[Candle] = []
        cursor = start_time
        while cursor < end_time:
            raw = self._client.klines(
                symbol=asset,
                interval=timeframe,
                startTime=int(cursor.timestamp() * 1000),
                endTime=int(end_time.timestamp() * 1000),
                limit=1000,
            )
            if not raw:
                break
            candles = _parse_klines_rows(asset, timeframe, raw)
            all_candles.extend(candles)
            next_cursor = candles[-1].close_time + timedelta(milliseconds=1)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(raw) < 1000:
                break
        return all_candles

    def symbol_exists(self, asset: str) -> bool:
        """Comprueba si el símbolo existe en Binance Spot vía `exchangeInfo`.

        # FIX (2026-07-07, fase 3 dashboard): para un símbolo que NO
        # existe, `exchange_info` no devuelve una lista vacía — Binance
        # responde HTTP 400 con `error_code=-1121` ("Invalid symbol").
        # Antes esta función dejaba propagar esa excepción sin capturarla,
        # así que un símbolo inválido tumbaba `scripts/analiza.py` (y el
        # botón "Analizar ahora" del dashboard, que llama a esto
        # precisamente para dar un "Rechazado: ... no existe" limpio) con
        # un traceback crudo en vez del mensaje de rechazo esperado.
        """
        try:
            info = self._client.exchange_info(symbol=asset)
        except ClientError as exc:
            if exc.error_code == -1121:
                return False
            raise
        return len(info.get("symbols", [])) > 0

    def get_min_notional(self, asset: str) -> Decimal:
        """Lee `exchangeInfo` (solo lectura, sin auth) para el filtro
        NOTIONAL/MIN_NOTIONAL del símbolo. Usado por el risk engine
        (`notional_min`, sección 9.1) y, en fase de ejecución, por
        `binance_executor.py` (sección 10.1) — misma función, sin duplicar.
        """
        info = self._client.exchange_info(symbol=asset)
        symbols = info.get("symbols", [])
        if not symbols:
            raise ValueError(f"symbol not found in exchangeInfo: {asset}")
        for f in symbols[0].get("filters", []):
            if f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
                value = f.get("minNotional") or f.get("notional")
                if value is not None:
                    return Decimal(str(value))
        raise ValueError(f"no NOTIONAL filter found for {asset}")

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
