"""Add email_classifications and email_feedback tables

Revision ID: 005
Revises: 004
Create Date: 2026-04-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_classifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("graph_message_id", sa.String, unique=True, nullable=False),
        sa.Column("needs_reply", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("classified_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "email_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("graph_message_id", sa.String, unique=True, nullable=False),
        sa.Column("subject", sa.String, nullable=False),
        sa.Column("sender_address", sa.String, nullable=False),
        sa.Column("sender_name", sa.String, nullable=False, server_default=""),
        sa.Column("body_preview", sa.Text, nullable=False, server_default=""),
        sa.Column("decision", sa.String, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("email_feedback")
    op.drop_table("email_classifications")
