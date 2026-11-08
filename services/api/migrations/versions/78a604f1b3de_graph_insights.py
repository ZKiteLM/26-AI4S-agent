"""Store graph-specific reviews and future-work explorations.

Revision ID: 78a604f1b3de
Revises: 20638ed999d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "78a604f1b3de"
down_revision: str | Sequence[str] | None = "20638ed999d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_insights",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "graph_id", sa.String(length=36), sa.ForeignKey("graph_views.id"), nullable=False
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("graph_id", "kind"),
    )
    op.create_index("ix_graph_insights_graph_id", "graph_insights", ["graph_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_insights_graph_id", table_name="graph_insights")
    op.drop_table("graph_insights")
