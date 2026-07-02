"""Descarga histórico de 2+ años de velas 1h/4h del universo para
backtesting walk-forward (sección 14). Persiste en `candles` vía upsert
idempotente — se puede re-ejecutar sin duplicar filas ni perder progreso
si se corta a mitad.

Uso (con el stack levantado):
    docker compose exec app uv run python -m backtests.download_history --days 800
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from core.logging import configure_logging, get_logger
from db.session import get_session
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import upsert_asset, upsert_candles

logger = get_logger("download_history")

TIMEFRAMES = ("1h", "4h")


async def main(days: int) -> None:
    settings = get_settings()
    client = BinanceMarketData()
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)

    assets = list(settings.universe_list)
    if "BTCUSDT" not in assets:
        assets.append("BTCUSDT")  # referencia de buy&hold, sección 3.2/14

    async with get_session() as session:
        for asset in assets:
            await upsert_asset(session, asset)
            for timeframe in TIMEFRAMES:
                candles = await asyncio.to_thread(client.fetch_klines_range, asset, timeframe, start, end)
                await upsert_candles(session, candles)
                await session.commit()
                print(f"{asset} {timeframe}: {len(candles)} velas ({start.date()} -> {end.date()})")

    print("Descarga completa.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga histórico para backtesting")
    parser.add_argument("--days", type=int, default=800, help="Ventana hacia atrás desde hoy (default 800 ~ 2.2 años)")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(main(args.days))
