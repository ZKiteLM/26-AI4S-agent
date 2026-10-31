"""Persistent conversations and turns.

Revision ID: 8b62ca1a4e70
Revises: 7d20cc326618
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b62ca1a4e70"
down_revision: str | Sequence[str] | None = "7d20cc326618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("paper_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_sessions_owner_id", "chat_sessions", ["owner_id"])
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("session_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_agent_runs_session_id", "chat_sessions", ["session_id"], ["id"]
        )
        batch.create_index("ix_agent_runs_session_id", ["session_id"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_session_id")
        batch.drop_constraint("fk_agent_runs_session_id", type_="foreignkey")
        batch.drop_column("session_id")
    op.drop_index("ix_chat_sessions_owner_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
