"""One-time cleanup: delete cached classifications that the old classifier
prompt got wrong, so they re-evaluate with the current prompt. Targets two
known failure modes:
  1. Salutation fast-path mismatches (confidence = 0.10 + "likely intended
     for someone else") — the old regex tripped on things like "Hi If you..."
     and the old compare missed nicknames like "Phil" vs "Phillip".
  2. Claude classifications that said needs_reply=False because the email
     required an action (sign, review, approve) rather than a written reply —
     the new prompt treats any required action as needs_reply=True.

Revision ID: 008
Revises: 007
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Salutation fast-path false positives — only the fast-path sets confidence
    # to exactly 0.10 with this exact reasoning suffix.
    op.execute(
        "DELETE FROM email_classifications "
        "WHERE needs_reply = 0 "
        "AND confidence = 0.10 "
        "AND reasoning LIKE '%likely intended for someone else%'"
    )
    # Action-required emails that the old prompt wrongly said didn't need a reply.
    op.execute(
        "DELETE FROM email_classifications "
        "WHERE needs_reply = 0 "
        "AND ("
        "reasoning LIKE '%not necessarily a written reply%' "
        "OR reasoning LIKE '%not necessarily a reply%' "
        "OR reasoning LIKE '%doesn''t necessarily require a reply%' "
        "OR reasoning LIKE '%does not necessarily require a reply%' "
        "OR reasoning LIKE '%no explicit reply needed%'"
        ")"
    )


def downgrade() -> None:
    # Deletions are idempotent — the next classify() call rebuilds the cache.
    pass
