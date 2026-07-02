"""Persistencia de datos de mercado. Upsert idempotente: reingestar la misma
vela no debe duplicar filas ni fallar."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.market import Candle, MarketSnapshot
from db.models import Asset
from db.models import Candle as CandleRow
from db.models import MarketSnapshot as MarketSnapshotRow

TIMEFRAME_DURATION = {"1h": timedelta(hours=1), "4h": timedelta(hours=4)}


async def upsert_asset(session: AsyncSession, symbol: str, quote: str = "USDT") -> None:
    base = symbol.removesuffix(quote) if symbol.endswith(quote) else symbol
    stmt = (
        pg_insert(Asset)
        .values(symbol=symbol, base=base, quote=quote, active=True)
        .on_conflict_do_update(
            index_elements=[Asset.symbol],
            set_={"base": base, "quote": quote, "active": True},
        )
    )
    await session.execute(stmt)


# asyncpg rechaza más de 32767 parámetros por query; 9 columnas/fila ->
# tope teórico ~3640 filas. Se deja margen amplio para no rozarlo nunca.
CANDLE_UPSERT_BATCH_SIZE = 2000


async def upsert_candles(session: AsyncSession, candles: list[Candle]) -> None:
    if not candles:
        return
    rows = [
        {
            "asset": c.asset,
            "timeframe": c.timeframe,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "quote_volume": c.quote_volume,
        }
        for c in candles
    ]
    for i in range(0, len(rows), CANDLE_UPSERT_BATCH_SIZE):
        batch = rows[i : i + CANDLE_UPSERT_BATCH_SIZE]
        stmt = pg_insert(CandleRow).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CandleRow.asset, CandleRow.timeframe, CandleRow.open_time],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "quote_volume": stmt.excluded.quote_volume,
            },
        )
        await session.execute(stmt)


async def insert_market_snapshots(session: AsyncSession, snapshots: list[MarketSnapshot]) -> None:
    if not snapshots:
        return
    rows = [
        {
            "asset": s.asset,
            "ts": s.ts,
            "bid": s.bid,
            "ask": s.ask,
            "spread_bps": s.spread_bps,
            "quote_vol_24h": s.quote_vol_24h,
            "change_24h_pct": s.change_24h_pct,
            "raw_jsonb": s.raw,
        }
        for s in snapshots
    ]
    await session.execute(pg_insert(MarketSnapshotRow).values(rows))


async def latest_candle_open_time(session: AsyncSession, asset: str, timeframe: str):
    stmt = (
        select(CandleRow.open_time)
        .where(CandleRow.asset == asset, CandleRow.timeframe == timeframe)
        .order_by(CandleRow.open_time.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _row_to_candle(row: CandleRow) -> Candle:
    """`close_time` no se guarda en DB (sección 16); se reconstruye como
    `open_time + duración del timeframe` — igual o ligeramente posterior
    al cierre real de Binance, nunca antes (conservador, anti look-ahead)."""
    duration = TIMEFRAME_DURATION[row.timeframe]
    return Candle(
        asset=row.asset,
        timeframe=row.timeframe,
        open_time=row.open_time,
        close_time=row.open_time + duration,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        quote_volume=row.quote_volume,
    )


async def get_recent_candles(
    session: AsyncSession, asset: str, timeframe: str, limit: int = 500
) -> list[Candle]:
    """Lee las últimas `limit` velas ya persistidas, ascendente por
    open_time."""
    stmt = (
        select(CandleRow)
        .where(CandleRow.asset == asset, CandleRow.timeframe == timeframe)
        .order_by(CandleRow.open_time.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(reversed(result.scalars().all()))
    return [_row_to_candle(row) for row in rows]


async def get_all_candles(session: AsyncSession, asset: str, timeframe: str) -> list[Candle]:
    """Lee TODO el histórico persistido de un asset/timeframe, ascendente
    por open_time. Uso: backtesting (sección 14), no el pipeline en vivo."""
    stmt = (
        select(CandleRow)
        .where(CandleRow.asset == asset, CandleRow.timeframe == timeframe)
        .order_by(CandleRow.open_time.asc())
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [_row_to_candle(row) for row in rows]


async def get_latest_snapshot(session: AsyncSession, asset: str) -> MarketSnapshot | None:
    stmt = (
        select(MarketSnapshotRow)
        .where(MarketSnapshotRow.asset == asset)
        .order_by(MarketSnapshotRow.ts.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return MarketSnapshot(
        asset=row.asset,
        ts=row.ts,
        bid=row.bid,
        ask=row.ask,
        spread_bps=row.spread_bps,
        quote_vol_24h=row.quote_vol_24h,
        change_24h_pct=row.change_24h_pct,
        raw=row.raw_jsonb,
    )
