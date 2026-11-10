"""Store per-user model endpoint preferences with encrypted API keys.

Revision ID: 6a93c1f8e204
Revises: 9c51d1a7f3e2
"""

import sqlalchemy as sa
from alembic import op

revision = "6a93c1f8e204"
down_revision = "9c51d1a7f3e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_model_configs",
        sa.Column("owner_id", sa.String(80), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("user_model_configs")
