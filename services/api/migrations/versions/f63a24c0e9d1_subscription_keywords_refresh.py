"""Persist subscription keywords and per-subscription refresh state.

Revision ID: f63a24c0e9d1
Revises: e18b9f67a402
"""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "f63a24c0e9d1"
down_revision = "e18b9f67a402"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in (
        ("keywords", sa.JSON()),
        ("last_auto_refreshed_at", sa.DateTime(timezone=True)),
        ("next_refresh_at", sa.DateTime(timezone=True)),
        ("refresh_status", sa.String(20)),
        ("refresh_token", sa.String(36)),
        ("refresh_mode", sa.String(20)),
        ("refresh_requested_at", sa.DateTime(timezone=True)),
        ("refresh_error", sa.Text()),
        ("deleted_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("subscriptions", sa.Column(name, kind, nullable=True))
    op.add_column("recommendations", sa.Column("matched_keywords", sa.JSON(), nullable=True))
    connection = op.get_bind()
    subscriptions_table = sa.table(
        "subscriptions",
        sa.column("id", sa.String()),
        sa.column("keywords", sa.JSON()),
        sa.column("last_auto_refreshed_at", sa.DateTime(timezone=True)),
        sa.column("next_refresh_at", sa.DateTime(timezone=True)),
        sa.column("refresh_status", sa.String()),
    )
    recommendations_table = sa.table("recommendations", sa.column("matched_keywords", sa.JSON()))
    subscriptions = connection.execute(
        sa.text("SELECT id, topic, last_checked_at FROM subscriptions")
    ).all()
    for row in subscriptions:
        last = row.last_checked_at
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        connection.execute(
            sa.update(subscriptions_table)
            .where(subscriptions_table.c.id == row.id)
            .values(
                keywords=[row.topic],
                last_auto_refreshed_at=last,
                next_refresh_at=last + timedelta(days=7) if last else datetime.now(UTC),
                refresh_status="idle",
            )
        )
    connection.execute(sa.update(recommendations_table).values(matched_keywords={}))
    with op.batch_alter_table("subscriptions") as batch:
        batch.alter_column("keywords", nullable=False)
        batch.alter_column("refresh_status", nullable=False)
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("matched_keywords", nullable=False)


def downgrade():
    op.drop_column("recommendations", "matched_keywords")
    for name in (
        "deleted_at",
        "refresh_error",
        "refresh_requested_at",
        "refresh_mode",
        "refresh_token",
        "refresh_status",
        "next_refresh_at",
        "last_auto_refreshed_at",
        "keywords",
    ):
        op.drop_column("subscriptions", name)
