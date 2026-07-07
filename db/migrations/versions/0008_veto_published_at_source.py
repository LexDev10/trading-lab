"""fix bug #18 (docs/CODE_REVIEW_2026-07-07.md): published_at/source en
item_classifications para medir la ventana de veto y permitir
corroboración por fuentes independientes

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("item_classifications", sa.Column("published_at", TIMESTAMPTZ, nullable=True))
    op.add_column("item_classifications", sa.Column("source", sa.String(60), nullable=True))
    # Backfill best-effort para filas existentes: join polimórfico contra
    # news_items/social_items por (item_kind, item_id). No es crítico (el
    # código nuevo hace COALESCE(published_at, classified_at) si queda
    # NULL) pero deja el histórico útil para el scorecard/dashboard.
    op.execute(
        """
        UPDATE item_classifications ic
        SET published_at = ni.published_at, source = ni.source
        FROM news_items ni
        WHERE ic.item_kind = 'news' AND ic.item_id = ni.id AND ic.published_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE item_classifications ic
        SET published_at = si.published_at, source = 'reddit/' || si.subreddit
        FROM social_items si
        WHERE ic.item_kind = 'social' AND ic.item_id = si.id AND ic.published_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("item_classifications", "source")
    op.drop_column("item_classifications", "published_at")
