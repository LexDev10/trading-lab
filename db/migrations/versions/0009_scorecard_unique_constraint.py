"""fix bug #14 (docs/CODE_REVIEW_2026-07-07.md): constraint único en
classifier_scorecard para permitir upsert por (week, stance, horizon)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-07

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DECISION: `classifier_scorecard` es una tabla DERIVADA/recalculable
    # (no almacén PIT) — re-ejecutar el job semanal para la misma semana
    # ahora hace upsert en vez de duplicar filas (bug #14, punto 2). Antes
    # de crear el constraint, se eliminan duplicados existentes
    # quedándonos con la fila de mayor `id` (la más reciente) por grupo.
    op.execute(
        """
        DELETE FROM classifier_scorecard a
        USING classifier_scorecard b
        WHERE a.id < b.id
          AND a.week = b.week AND a.stance = b.stance AND a.horizon = b.horizon
        """
    )
    op.create_unique_constraint(
        "uq_classifier_scorecard_week_stance_horizon",
        "classifier_scorecard",
        ["week", "stance", "horizon"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_classifier_scorecard_week_stance_horizon", "classifier_scorecard", type_="unique"
    )
