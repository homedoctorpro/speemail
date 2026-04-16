"""Remove auto-send functionality

Revision ID: 002
Revises: 001
Create Date: 2026-04-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("auto_send_log")

    settings = sa.table("settings", sa.column("key", sa.String))
    op.execute(
        settings.delete().where(
            settings.c.key.in_(["auto_send_enabled", "auto_send_threshold"])
        )
    )


def downgrade() -> None:
    op.create_table(
        "auto_send_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "tracked_email_id",
            sa.Integer,
            sa.ForeignKey("tracked_emails.id"),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.bulk_insert(
        sa.table(
            "settings",
            sa.column("key", sa.String),
            sa.column("value", sa.Text),
        ),
        [
            {"key": "auto_send_enabled", "value": "false"},
            {"key": "auto_send_threshold", "value": "0.95"},
        ],
    )
