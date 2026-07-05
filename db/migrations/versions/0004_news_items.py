"""fase 2 (arranque): news_items — almacén point-in-time inmutable

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("asset_tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("raw_jsonb", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_news_items_fetched_at", "news_items", ["fetched_at"])
    op.create_index("ix_news_items_source", "news_items", ["source"])


def downgrade() -> None:
    op.drop_index("ix_news_items_source", table_name="news_items")
    op.drop_index("ix_news_items_fetched_at", table_name="news_items")
    op.drop_table("news_items")
