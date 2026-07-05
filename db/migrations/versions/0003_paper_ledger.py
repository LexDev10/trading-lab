"""fase 1: paper ledger interno — timeframe/horizon_class en trade_entries

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NUMERIC = sa.Numeric(precision=28, scale=10)


def upgrade() -> None:
    # `asset` NOT NULL sin server_default: seguro porque `trade_entries`
    # está vacía en todo despliegue existente (ningún código la ha escrito
    # nunca — ver # DECISION en services/execution/paper_ledger.py).
    op.add_column("trade_entries", sa.Column("asset", sa.String(20), nullable=False))
    op.add_column("trade_entries", sa.Column("timeframe", sa.String(2), nullable=True))
    op.add_column("trade_entries", sa.Column("horizon_class", sa.String(10), nullable=True))
    op.add_column("trade_entries", sa.Column("invalidation_level", NUMERIC, nullable=True))


def downgrade() -> None:
    op.drop_column("trade_entries", "invalidation_level")
    op.drop_column("trade_entries", "horizon_class")
    op.drop_column("trade_entries", "timeframe")
    op.drop_column("trade_entries", "asset")
