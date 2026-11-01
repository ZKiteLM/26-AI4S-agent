"""Keep the total recommendation score unknown until semantic analysis succeeds.

Revision ID: d43c9e72b5f1
Revises: c91f06b2e4a7
"""

import sqlalchemy as sa
from alembic import op

revision = "d43c9e72b5f1"
down_revision = "c91f06b2e4a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE recommendations SET score = 0 WHERE score IS NULL")
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("score", existing_type=sa.Float(), nullable=False)
