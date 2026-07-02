"""Modelos ORM (SQLAlchemy 2.0). Reflejan el esquema de la sección 16 de
ESPECIFICACION_SISTEMA_TRADING.md. Solo se declaran aquí las tablas que
corresponden a fases ya implementadas (fase 0: assets, candles,
market_snapshots)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric, PrimaryKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


NUMERIC = Numeric(precision=28, scale=10)
TIMESTAMPTZ = DateTime(timezone=True)


class Asset(Base):
    __tablename__ = "assets"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    base: Mapped[str] = mapped_column(String(10))
    quote: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (PrimaryKeyConstraint("asset", "timeframe", "open_time"),)

    asset: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(2))
    open_time: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    open: Mapped[Decimal] = mapped_column(NUMERIC)
    high: Mapped[Decimal] = mapped_column(NUMERIC)
    low: Mapped[Decimal] = mapped_column(NUMERIC)
    close: Mapped[Decimal] = mapped_column(NUMERIC)
    volume: Mapped[Decimal] = mapped_column(NUMERIC)
    quote_volume: Mapped[Decimal] = mapped_column(NUMERIC)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String(20))
    ts: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    bid: Mapped[Decimal] = mapped_column(NUMERIC)
    ask: Mapped[Decimal] = mapped_column(NUMERIC)
    spread_bps: Mapped[Decimal] = mapped_column(NUMERIC)
    quote_vol_24h: Mapped[Decimal] = mapped_column(NUMERIC)
    change_24h_pct: Mapped[Decimal] = mapped_column(NUMERIC)
    raw_jsonb: Mapped[dict] = mapped_column(JSONB)
