"""Separate discovery candidates from the personal paper library.

Revision ID: c91f06b2e4a7
Revises: 8b62ca1a4e70
"""

import re
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "c91f06b2e4a7"
down_revision = "8b62ca1a4e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("last_checked_at", sa.DateTime(timezone=True)))
    op.create_table(
        "paper_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title_key", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("year", sa.Integer()),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("doi", sa.String(255)),
        sa.Column("arxiv_id", sa.String(80)),
        sa.Column("openalex_id", sa.String(100)),
        sa.Column("pdf_url", sa.Text()),
        sa.Column("github_url", sa.Text()),
        sa.Column("citation_count", sa.Integer()),
        sa.Column("github_stars", sa.Integer()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for field in ("doi", "arxiv_id", "openalex_id"):
        op.create_index(f"ix_paper_candidates_{field}", "paper_candidates", [field])
    with op.batch_alter_table("recommendations") as batch:
        batch.add_column(sa.Column("candidate_id", sa.String(36)))
        batch.add_column(sa.Column("topics", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("relevance_score", sa.Float()))
        batch.add_column(sa.Column("value_score", sa.Float()))
        batch.add_column(sa.Column("summary", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("reason", sa.Text(), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("status", sa.String(20), nullable=False, server_default="pending")
        )
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True)))
    conn = op.get_bind()
    rows = (
        conn.execute(
            sa.text(
                "SELECT r.id, r.paper_id, p.title, p.authors, p.abstract, p.year, p.source, "
                "p.doi, p.arxiv_id, p.openalex_id, r.reasons FROM recommendations r "
                "JOIN papers p ON p.id = r.paper_id"
            )
        )
        .mappings()
        .all()
    )
    candidate_by_key = {}
    timestamp = datetime.now(UTC)
    for row in rows:
        key = re.sub(r"\W+", "", row["title"].casefold()) or row["paper_id"]
        if key not in candidate_by_key:
            candidate_by_key[key] = row["paper_id"]
            conn.execute(
                sa.text(
                    "INSERT INTO paper_candidates (id, title_key, title, authors, abstract, year, source, "
                    "doi, arxiv_id, openalex_id, first_seen_at, updated_at) VALUES "
                    "(:id, :key, :title, :authors, :abstract, :year, :source, :doi, :arxiv_id, "
                    ":openalex_id, :at, :at)"
                ),
                {**row, "id": row["paper_id"], "key": key, "at": timestamp},
            )
        conn.execute(
            sa.text(
                "UPDATE recommendations SET candidate_id=:candidate, created_at=:at, "
                "status='imported' WHERE id=:id"
            ),
            {"candidate": candidate_by_key[key], "at": timestamp, "id": row["id"]},
        )
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("candidate_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("paper_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_foreign_key(
            "fk_recommendations_candidate_id", "paper_candidates", ["candidate_id"], ["id"]
        )
        batch.create_index("ix_recommendations_candidate_id", ["candidate_id"])
        batch.create_unique_constraint(
            "uq_recommendations_owner_candidate", ["owner_id", "candidate_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.drop_constraint("uq_recommendations_owner_candidate", type_="unique")
        batch.drop_index("ix_recommendations_candidate_id")
        batch.drop_constraint("fk_recommendations_candidate_id", type_="foreignkey")
        for column in (
            "candidate_id",
            "topics",
            "relevance_score",
            "value_score",
            "summary",
            "reason",
            "status",
            "created_at",
        ):
            batch.drop_column(column)
        batch.alter_column("paper_id", existing_type=sa.String(36), nullable=False)
    for field in ("doi", "arxiv_id", "openalex_id"):
        op.drop_index(f"ix_paper_candidates_{field}", table_name="paper_candidates")
    op.drop_table("paper_candidates")
    op.drop_column("subscriptions", "last_checked_at")
