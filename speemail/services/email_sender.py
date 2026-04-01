"""
Send emails via Microsoft Graph API.
Uses the createReply endpoint so the sent email is threaded correctly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import TrackedEmail

logger = logging.getLogger(__name__)


class SendError(Exception):
    pass


def send_reply(
    client: GraphClient,
    db: Session,
    email: TrackedEmail,
) -> None:
    """
    Send the approved draft as a reply to the original email thread.
    Updates email.status and email.final_sent_at on success.
    Raises SendError on failure.
    """
    if email.status == "sent":
        raise SendError(f"Email {email.id} is already sent")

    body_text = email.effective_body()
    subject = email.ai_draft_subject or f"Re: {email.original_subject}"

    if not body_text:
        raise SendError(f"No draft body available for email {email.id}")

    signature = _get_signature(db)
    if signature:
        body_text = f"{body_text}\n\n{signature}"

    try:
        # Step 1: Create a reply draft from the original message
        reply_draft = client.post(
            f"/me/messages/{email.graph_message_id}/createReply",
            body={
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "text",
                        "content": body_text,
                    },
                }
            },
        )

        reply_id = reply_draft.get("id")
        if not reply_id:
            raise SendError("Graph API did not return a reply draft ID")

        # Step 2: Send the draft
        client.post(f"/me/messages/{reply_id}/send", body={})

    except SendError:
        raise
    except Exception as exc:
        raise SendError(f"Graph API error sending email {email.id}: {exc}") from exc

    email.status = "sent"
    email.final_sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("Sent reply for email %d: %s", email.id, email.original_subject)


def _get_signature(db: Session) -> str:
    """Fetch the user's email signature from settings."""
    from speemail.models.tables import Setting
    row = db.query(Setting).filter_by(key="email_signature").first()
    return (row.value or "").strip() if row else ""
