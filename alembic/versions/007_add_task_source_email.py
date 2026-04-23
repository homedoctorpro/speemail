"""Add source_graph_message_id to tasks for auto-generated tasks from emails

Revision ID: 007
Revises: 6250de3c6c8c
Create Date: 2026-04-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "6250de3c6c8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("source_graph_message_id", sa.String, nullable=True),
    )
    op.create_index(
        "ix_tasks_source_graph_message_id",
        "tasks",
        ["source_graph_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_source_graph_message_id", table_name="tasks")
    op.drop_column("tasks", "source_graph_message_id")
