"""fase 1: regime_log, decision_logs, trade_entries, trade_exits,
position_events, equity_snapshots, system_state

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUMERIC = sa.Numeric(precision=28, scale=10)


def upgrade() -> None:
    op.create_table(
        "regime_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("btc_regime", sa.String(20), nullable=False),
        sa.Column("atr_pct", NUMERIC, nullable=False),
        sa.Column("details_jsonb", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_regime_log_ts", "regime_log", ["ts"])

    op.create_table(
        "decision_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("git_sha", sa.String(40), nullable=False),
        sa.Column("scanner_jsonb", postgresql.JSONB, nullable=False),
        sa.Column("technical_jsonb", postgresql.JSONB, nullable=True),
        sa.Column("fundamental_jsonb", postgresql.JSONB, nullable=True),
        sa.Column("decision_jsonb", postgresql.JSONB, nullable=True),
        sa.Column("risk_verdict_jsonb", postgresql.JSONB, nullable=True),
        sa.Column("final_action", sa.String(20), nullable=False),
        sa.Column("rejection_reasons", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("expected_tp", NUMERIC, nullable=True),
        sa.Column("expected_sl", NUMERIC, nullable=True),
        sa.Column("horizon_class", sa.String(10), nullable=True),
    )
    op.create_index("ix_decision_logs_asset_ts", "decision_logs", ["asset", "ts"])
    op.create_index("ix_decision_logs_final_action", "decision_logs", ["final_action"])

    op.create_table(
        "trade_entries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("decision_log_id", sa.BigInteger, sa.ForeignKey("decision_logs.id"), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("client_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("exchange_order_id", sa.String(64), nullable=True),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", NUMERIC, nullable=False),
        sa.Column("qty", NUMERIC, nullable=False),
        sa.Column("tp", NUMERIC, nullable=False),
        sa.Column("sl", NUMERIC, nullable=False),
        sa.Column("oco_list_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_index("ix_trade_entries_status", "trade_entries", ["status"])

    op.create_table(
        "trade_exits",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_entry_id", sa.BigInteger, sa.ForeignKey("trade_entries.id"), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", NUMERIC, nullable=False),
        sa.Column("exit_qty", NUMERIC, nullable=False),
        sa.Column("exit_type", sa.String(20), nullable=False),
        sa.Column("fees_paid", NUMERIC, nullable=False),
        sa.Column("pnl_quote", NUMERIC, nullable=False),
        sa.Column("pnl_pct_net", NUMERIC, nullable=False),
    )
    op.create_index("ix_trade_exits_exit_time", "trade_exits", ["exit_time"])

    op.create_table(
        "position_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_entry_id", sa.BigInteger, sa.ForeignKey("trade_entries.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("payload_jsonb", postgresql.JSONB, nullable=False),
    )

    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("equity_quote", NUMERIC, nullable=False),
        sa.Column("open_positions", sa.Integer, nullable=False),
        sa.Column("drawdown_pct", NUMERIC, nullable=False),
    )
    op.create_index("ix_equity_snapshots_ts", "equity_snapshots", ["ts"])

    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("state", sa.String(10), nullable=False, server_default="running"),
        sa.Column("halted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("halted_reason", sa.String(200), nullable=True),
        sa.Column("rearmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_state")
    op.drop_index("ix_equity_snapshots_ts", table_name="equity_snapshots")
    op.drop_table("equity_snapshots")
    op.drop_table("position_events")
    op.drop_index("ix_trade_exits_exit_time", table_name="trade_exits")
    op.drop_table("trade_exits")
    op.drop_index("ix_trade_entries_status", table_name="trade_entries")
    op.drop_table("trade_entries")
    op.drop_index("ix_decision_logs_final_action", table_name="decision_logs")
    op.drop_index("ix_decision_logs_asset_ts", table_name="decision_logs")
    op.drop_table("decision_logs")
    op.drop_index("ix_regime_log_ts", table_name="regime_log")
    op.drop_table("regime_log")
