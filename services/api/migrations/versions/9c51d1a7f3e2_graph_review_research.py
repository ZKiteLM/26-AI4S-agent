"""Persist graph review research checkpoints and evidence.

Revision ID: 9c51d1a7f3e2
Revises: 78a604f1b3de
"""

import sqlalchemy as sa
from alembic import op

revision = "9c51d1a7f3e2"
down_revision = "78a604f1b3de"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "graph_review_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("graph_id", sa.String(36), sa.ForeignKey("graph_views.id"), nullable=False),
        sa.Column("owner_id", sa.String(80), nullable=False),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("background_tasks.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("outline", sa.JSON(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_graph_review_runs_graph_id", "graph_review_runs", ["graph_id"])
    op.create_index("ix_graph_review_runs_owner_id", "graph_review_runs", ["owner_id"])
    op.create_table(
        "graph_review_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("graph_review_runs.id"), nullable=False),
        sa.Column("work_id", sa.String(36), sa.ForeignKey("scholarly_works.id"), nullable=False),
        sa.Column("paper_id", sa.String(36)),
        sa.Column("version_id", sa.String(36)),
        sa.Column("block_id", sa.String(36)),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("page_index", sa.Integer()),
        sa.Column("start", sa.Integer()),
        sa.Column("length", sa.Integer(), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
    )
    op.create_index("ix_graph_review_evidence_run_id", "graph_review_evidence", ["run_id"])
    op.create_index("ix_graph_review_evidence_work_id", "graph_review_evidence", ["work_id"])


def downgrade():
    op.drop_table("graph_review_evidence")
    op.drop_table("graph_review_runs")
