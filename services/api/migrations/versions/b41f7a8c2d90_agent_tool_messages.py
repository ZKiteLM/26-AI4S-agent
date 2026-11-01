"""Persist assistant tool calls and paired tool results.

Revision ID: b41f7a8c2d90
Revises: a20d1b8e5c74
"""

import sqlalchemy as sa
from alembic import op

revision = "b41f7a8c2d90"
down_revision = "a20d1b8e5c74"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_messages", sa.Column("tool_calls", sa.JSON(), nullable=True))
    op.add_column("chat_messages", sa.Column("tool_call_id", sa.String(120), nullable=True))
    op.add_column("chat_messages", sa.Column("tool_name", sa.String(80), nullable=True))


def downgrade():
    op.drop_column("chat_messages", "tool_name")
    op.drop_column("chat_messages", "tool_call_id")
    op.drop_column("chat_messages", "tool_calls")
