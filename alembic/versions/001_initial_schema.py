"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tracked_emails",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("graph_message_id", sa.String, unique=True, nullable=False),
        sa.Column("graph_conversation_id", sa.String, nullable=False, index=True),
        sa.Column("email_type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending_approval"),
        sa.Column("original_subject", sa.String, nullable=False),
        sa.Column("original_from", sa.String, nullable=False),
        sa.Column("original_to", sa.String, nullable=True),
        sa.Column("original_body_preview", sa.Text, nullable=True),
        sa.Column("original_body_html", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.Column("ai_draft_subject", sa.String, nullable=True),
        sa.Column("ai_draft_body", sa.Text, nullable=True),
        sa.Column("ai_confidence_score", sa.Float, nullable=True),
        sa.Column("ai_reasoning", sa.Text, nullable=True),
        sa.Column("user_edited_body", sa.Text, nullable=True),
        sa.Column("final_sent_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "poll_cursors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cursor_name", sa.String, unique=True, nullable=False),
        sa.Column("last_checked", sa.DateTime, nullable=False),
    )

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

    op.create_table(
        "settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )

    # Seed default settings
    op.bulk_insert(
        sa.table(
            "settings",
            sa.column("key", sa.String),
            sa.column("value", sa.Text),
        ),
        [
            {"key": "follow_up_days", "value": "3"},
            {"key": "poll_interval_minutes", "value": "15"},
            {"key": "auto_send_enabled", "value": "false"},
            {"key": "auto_send_threshold", "value": "0.95"},
            {"key": "email_signature", "value": ""},
        ],
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("auto_send_log")
    op.drop_table("poll_cursors")
    op.drop_table("tracked_emails")
