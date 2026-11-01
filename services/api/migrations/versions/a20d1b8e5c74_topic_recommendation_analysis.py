"""Keep topic-specific recommendation analyses.

Revision ID: a20d1b8e5c74
Revises: f63a24c0e9d1
"""

import sqlalchemy as sa
from alembic import op

revision = "a20d1b8e5c74"
down_revision = "f63a24c0e9d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recommendations", sa.Column("topic_analyses", sa.JSON(), nullable=True))
    table = sa.table(
        "recommendations",
        sa.column("id", sa.String()),
        sa.column("topics", sa.JSON()),
        sa.column("topic_analyses", sa.JSON()),
        sa.column("relevance_score", sa.Float()),
        sa.column("value_score", sa.Float()),
        sa.column("summary", sa.Text()),
        sa.column("reason", sa.Text()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).all():
        analysis = {}
        if (
            len(row.topics or []) == 1
            and row.relevance_score is not None
            and row.value_score is not None
        ):
            analysis[row.topics[0]] = {
                "relevance_score": row.relevance_score,
                "value_score": row.value_score,
                "summary": row.summary or "",
                "reason": row.reason or "",
            }
        connection.execute(
            sa.update(table).where(table.c.id == row.id).values(topic_analyses=analysis)
        )
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("topic_analyses", nullable=False)


def downgrade():
    op.drop_column("recommendations", "topic_analyses")
