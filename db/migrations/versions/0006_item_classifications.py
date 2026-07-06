"""fase 2: item_classifications + classifier_scorecard (sección 12.3/16)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_classifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.BigInteger, nullable=False),
        sa.Column("item_kind", sa.String(10), nullable=False),
        sa.Column("model_name", sa.String(60), nullable=False),
        sa.Column("model_version", sa.String(20), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("veto", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("output_jsonb", postgresql.JSONB, nullable=False),
        # DECISION (2026-07-06): no está en el schema sketch de la sección
        # 12.1, pero sin esto el check de veto por activo (sección 12.4)
        # exigiría un JOIN polimórfico contra news_items O social_items
        # según item_kind en cada ciclo del scanner. Se resuelve una vez,
        # en el momento de clasificar, y se guarda desnormalizado.
        sa.Column("asset_tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
    )
    op.create_index("ix_item_classifications_classified_at", "item_classifications", ["classified_at"])
    op.create_index(
        "ix_item_classifications_asset_tags", "item_classifications", ["asset_tags"], postgresql_using="gin"
    )
    op.create_index(
        "ix_item_classifications_item", "item_classifications", ["item_kind", "item_id"]
    )

    op.create_table(
        "classifier_scorecard",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("week", sa.Date, nullable=False),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("horizon", sa.String(5), nullable=False),
        sa.Column("n", sa.Integer, nullable=False),
        sa.Column("hit_rate", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("avg_fwd_return", sa.Numeric(precision=10, scale=6), nullable=False),
    )
    op.create_index("ix_classifier_scorecard_week", "classifier_scorecard", ["week"])


def downgrade() -> None:
    op.drop_index("ix_classifier_scorecard_week", table_name="classifier_scorecard")
    op.drop_table("classifier_scorecard")

    op.drop_index("ix_item_classifications_item", table_name="item_classifications")
    op.drop_index("ix_item_classifications_asset_tags", table_name="item_classifications")
    op.drop_index("ix_item_classifications_classified_at", table_name="item_classifications")
    op.drop_table("item_classifications")
