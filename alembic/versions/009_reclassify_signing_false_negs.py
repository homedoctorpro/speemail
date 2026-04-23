"""Broader reclassification cleanup: migration 008 used narrow substring
patterns and missed wording variations. This migration targets any cached
classification that (a) said needs_reply=False and (b) mentions signing,
review/approval, or the phrase "written reply" in its reasoning — these
are exactly the cases the updated prompt now treats as needs_reply=True.

Only deletes needs_reply=False rows, so we can't accidentally demote a
correct classification. Next classify() call regenerates them against
the current prompt.

Revision ID: 009
Revises: 008
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Wording patterns that indicate the old prompt treated a required action
# as "doesn't need a reply" — the classifier was literal-interpreting the
# field name. The new prompt explicitly counts signing/review/approval as
# needs_reply=true.
_SUSPECT_PATTERNS = [
    "%written reply%",
    "%sign the%",
    "%signing the%",
    "%signing a document%",
    "%signing documents%",
    "%action request%",
    "%requires an action%",
    "%requires action%",
    "%to sign %",
    "%for signature%",
    "%for your signature%",
    "%review and acknowledg%",
    "%review and let%",
    "%approval request%",
    "%PandaDoc%",
    "%DocuSign%",
    "%e-sign%",
    "%acknowledgment might be%",
    "%brief acknowledgment%",
]


def upgrade() -> None:
    for pattern in _SUSPECT_PATTERNS:
        op.execute(
            "DELETE FROM email_classifications "
            "WHERE needs_reply = 0 "
            f"AND reasoning LIKE '{pattern}'"
        )


def downgrade() -> None:
    pass
