"""fase 2: social_items — almacén point-in-time inmutable (Reddit)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "social_items",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("subreddit", sa.String(50), nullable=False),
        sa.Column("post_id", sa.String(20), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("score_at_fetch", sa.Integer, nullable=False),
        sa.Column("num_comments_at_fetch", sa.Integer, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_jsonb", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_social_items_fetched_at", "social_items", ["fetched_at"])
    op.create_index("ix_social_items_subreddit", "social_items", ["subreddit"])


def downgrade() -> None:
    op.drop_index("ix_social_items_subreddit", table_name="social_items")
    op.drop_index("ix_social_items_fetched_at", table_name="social_items")
    op.drop_table("social_items")
