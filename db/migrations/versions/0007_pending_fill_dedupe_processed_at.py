"""fix bugs #10/#11/#17 (docs/CODE_REVIEW_2026-07-07.md): dedupe de señal,
modelo de fill pendiente y processed_at en trade_exits

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NUMERIC = sa.Numeric(precision=28, scale=10)
TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Bug #11: límites de la entry_zone mientras `status='pending'`.
    op.add_column("trade_entries", sa.Column("entry_zone_low", NUMERIC, nullable=True))
    op.add_column("trade_entries", sa.Column("entry_zone_high", NUMERIC, nullable=True))
    # Bug #10: dedupe por vela de señal (asset, timeframe, signal_candle_close_time).
    op.add_column("trade_entries", sa.Column("signal_candle_close_time", TIMESTAMPTZ, nullable=True))
    op.create_index(
        "ix_trade_entries_signal_dedupe",
        "trade_entries",
        ["asset", "timeframe", "signal_candle_close_time"],
    )

    # Bug #17: tiempo de PROCESO del cierre, distinto de exit_time (tiempo
    # de vela). Nullable + backfill con exit_time (mejor aproximación
    # disponible para filas existentes) + NOT NULL.
    op.add_column("trade_exits", sa.Column("processed_at", TIMESTAMPTZ, nullable=True))
    op.execute("UPDATE trade_exits SET processed_at = exit_time WHERE processed_at IS NULL")
    op.alter_column("trade_exits", "processed_at", nullable=False)


def downgrade() -> None:
    op.drop_column("trade_exits", "processed_at")
    op.drop_index("ix_trade_entries_signal_dedupe", table_name="trade_entries")
    op.drop_column("trade_entries", "signal_candle_close_time")
    op.drop_column("trade_entries", "entry_zone_high")
    op.drop_column("trade_entries", "entry_zone_low")
