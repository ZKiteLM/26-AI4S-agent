"""Candidate code and citation evidence cache.

Revision ID: e18b9f67a402
Revises: d43c9e72b5f1
"""

from alembic import op
import sqlalchemy as sa

revision = "e18b9f67a402"
down_revision = "d43c9e72b5f1"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in (
        ("project_page_url", sa.Text()),
        ("github_source", sa.String(80)),
        ("github_verification", sa.String(20)),
        ("github_stars_updated_at", sa.DateTime(timezone=True)),
        ("code_checked_at", sa.DateTime(timezone=True)),
        ("code_check_status", sa.String(20)),
        ("citation_source", sa.String(40)),
        ("citation_updated_at", sa.DateTime(timezone=True)),
        ("citation_check_status", sa.String(20)),
    ):
        op.add_column("paper_candidates", sa.Column(name, kind, nullable=True))


def downgrade():
    for name in (
        "citation_check_status",
        "citation_updated_at",
        "citation_source",
        "code_check_status",
        "code_checked_at",
        "github_stars_updated_at",
        "github_verification",
        "github_source",
        "project_page_url",
    ):
        op.drop_column("paper_candidates", name)
