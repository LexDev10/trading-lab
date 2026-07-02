"""fase 0: assets, candles, market_snapshots

Revision ID: 0001
Revises:
Create Date: 2026-07-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUMERIC = sa.Numeric(precision=28, scale=10)


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("symbol", sa.String(20), primary_key=True),
        sa.Column("base", sa.String(10), nullable=False),
        sa.Column("quote", sa.String(10), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "candles",
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("timeframe", sa.String(2), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", NUMERIC, nullable=False),
        sa.Column("high", NUMERIC, nullable=False),
        sa.Column("low", NUMERIC, nullable=False),
        sa.Column("close", NUMERIC, nullable=False),
        sa.Column("volume", NUMERIC, nullable=False),
        sa.Column("quote_volume", NUMERIC, nullable=False),
        sa.PrimaryKeyConstraint("asset", "timeframe", "open_time"),
    )
    op.create_index("ix_candles_asset_timeframe", "candles", ["asset", "timeframe"])

    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bid", NUMERIC, nullable=False),
        sa.Column("ask", NUMERIC, nullable=False),
        sa.Column("spread_bps", NUMERIC, nullable=False),
        sa.Column("quote_vol_24h", NUMERIC, nullable=False),
        sa.Column("change_24h_pct", NUMERIC, nullable=False),
        sa.Column("raw_jsonb", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_market_snapshots_asset_ts", "market_snapshots", ["asset", "ts"])


def downgrade() -> None:
    op.drop_index("ix_market_snapshots_asset_ts", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_candles_asset_timeframe", table_name="candles")
    op.drop_table("candles")
    op.drop_table("assets")
