"""Persistencia de datos de mercado. Upsert idempotente: reingestar la misma
vela no debe duplicar filas ni fallar."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.market import Candle, MarketSnapshot
from db.models import Asset
from db.models import Candle as CandleRow
from db.models import MarketSnapshot as MarketSnapshotRow


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
    stmt = pg_insert(CandleRow).values(rows)
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
