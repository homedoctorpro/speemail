"""Add ignore_rules table and unresponded_scan_days setting

Revision ID: 004
Revises: 003
Create Date: 2026-04-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ignore_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rule_type", sa.String, nullable=False),
        sa.Column("pattern", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    # Default scan depth: 90 days (good for first-time setup)
    op.bulk_insert(
        sa.table("settings", sa.column("key", sa.String), sa.column("value", sa.Text)),
        [{"key": "unresponded_scan_days", "value": "90"}],
    )


def downgrade() -> None:
    op.drop_table("ignore_rules")
    settings = sa.table("settings", sa.column("key", sa.String))
    op.execute(settings.delete().where(settings.c.key == "unresponded_scan_days"))
