"""
Email polling logic.

Two modes:
  1. follow_up  — scans SentItems for emails without replies after N days
  2. quick_reply — scans Inbox for unread emails that may need a quick response
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.config import settings
from speemail.models.tables import PollCursor, TrackedEmail

logger = logging.getLogger(__name__)

GRAPH_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime(GRAPH_DATE_FMT)


def _parse_graph_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    # Graph returns ISO 8601 with or without timezone
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def _already_tracked(db: Session, graph_message_id: str) -> bool:
    return db.query(TrackedEmail).filter_by(graph_message_id=graph_message_id).first() is not None


def _get_cursor(db: Session, name: str) -> datetime:
    row = db.query(PollCursor).filter_by(cursor_name=name).first()
    if row:
        return row.last_checked
    # Default: look back FOLLOW_UP_DAYS + 2 days on first run
    return _utcnow() - timedelta(days=settings.follow_up_days + 2)


def _set_cursor(db: Session, name: str, dt: datetime) -> None:
    row = db.query(PollCursor).filter_by(cursor_name=name).first()
    if row:
        row.last_checked = dt
    else:
        db.add(PollCursor(cursor_name=name, last_checked=dt))


def _extract_addresses(recipients: list[dict]) -> str:
    """Convert Graph recipients list to comma-separated email addresses."""
    return ", ".join(
        r.get("emailAddress", {}).get("address", "") for r in (recipients or [])
    )


def _thread_has_reply(
    client: GraphClient,
    conversation_id: str,
    sent_at: datetime,
    user_email: str | None = None,
) -> bool:
    """
    Return True if someone OTHER than the user has sent a message in the conversation
    after sent_at. The user's own sent items have `receivedDateTime` a second or two
    past `sentDateTime`, so without the self-filter this function was returning True
    for every thread and silently disabling auto-watch.
    """
    sent_str = _fmt(sent_at)
    filter_q = (
        f"conversationId eq '{conversation_id}' "
        f"and receivedDateTime gt {sent_str}"
    )
    try:
        data = client.get(
            "/me/messages",
            params={
                "$filter": filter_q,
                "$select": "id,from,receivedDateTime",
                "$top": "10",
            },
        )
        msgs = data.get("value", [])
        if user_email:
            ue = user_email.lower()
            msgs = [
                m for m in msgs
                if m.get("from", {}).get("emailAddress", {}).get("address", "").lower() != ue
            ]
        return len(msgs) > 0
    except Exception as exc:
        logger.warning("Thread reply check failed for %s: %s", conversation_id, exc)
        return True  # Assume replied to avoid false follow-ups on errors


def poll_follow_ups(client: GraphClient, db: Session) -> list[TrackedEmail]:
    """
    Scan SentItems for emails without a reply after FOLLOW_UP_DAYS days.
    Returns new TrackedEmail rows (not yet committed).
    """
    from speemail.models.tables import Setting
    cutoff = _utcnow() - timedelta(days=settings.follow_up_days)
    cutoff_str = _fmt(cutoff)
    user_email_row = db.query(Setting).filter_by(key="user_email").first()
    user_email = user_email_row.value if user_email_row else None

    logger.info("Polling sent items for follow-ups (sent before %s)", cutoff_str)

    try:
        data = client.get(
            "/me/mailFolders/SentItems/messages",
            params={
                "$filter": f"sentDateTime le {cutoff_str}",
                "$select": (
                    "id,subject,conversationId,sentDateTime,"
                    "toRecipients,bodyPreview,body"
                ),
                "$top": "20",
            },
        )
        messages = data.get("value", [])
        # Client-side sort — corporate Exchange silently drops $orderby.
        messages.sort(key=lambda m: m.get("sentDateTime", ""), reverse=True)
    except Exception as exc:
        logger.error("Failed to fetch sent items: %s", exc)
        return []

    new_rows: list[TrackedEmail] = []
    for msg in messages:
        msg_id = msg.get("id", "")
        if not msg_id or _already_tracked(db, msg_id):
            continue

        conversation_id = msg.get("conversationId", "")
        sent_at_str = msg.get("sentDateTime")
        sent_at = _parse_graph_dt(sent_at_str) or _utcnow()

        # Skip if reply exists
        if _thread_has_reply(client, conversation_id, sent_at, user_email):
            continue

        body_content = msg.get("body", {}).get("content", "")
        row = TrackedEmail(
            graph_message_id=msg_id,
            graph_conversation_id=conversation_id,
            email_type="follow_up",
            status="pending_approval",
            original_subject=msg.get("subject", "(no subject)"),
            original_from="me",
            original_to=_extract_addresses(msg.get("toRecipients", [])),
            original_body_preview=msg.get("bodyPreview", ""),
            original_body_html=body_content,
            sent_at=sent_at,
        )
        db.add(row)
        new_rows.append(row)
        logger.info("Flagged for follow-up: %s", row.original_subject)
        if len(new_rows) >= 5:
            break

    return new_rows


def poll_quick_replies(client: GraphClient, db: Session) -> list[TrackedEmail]:
    """
    Scan Inbox for unread emails received since the last cursor.
    Returns new TrackedEmail rows (not yet committed) for AI to evaluate.
    """
    cursor_name = "inbox_quick_reply"
    since = _get_cursor(db, cursor_name)
    since_str = _fmt(since)
    now = _utcnow()

    logger.info("Polling inbox for quick replies (since %s)", since_str)

    try:
        data = client.get(
            "/me/mailFolders/Inbox/messages",
            params={
                "$filter": (
                    f"isRead eq false and receivedDateTime gt {since_str}"
                ),
                "$select": (
                    "id,subject,conversationId,from,receivedDateTime,"
                    "bodyPreview,body"
                ),
                "$top": "10",
            },
        )
        messages = data.get("value", [])
    except Exception as exc:
        logger.error("Failed to fetch inbox: %s", exc)
        return []

    new_rows: list[TrackedEmail] = []
    for msg in messages:
        msg_id = msg.get("id", "")
        if not msg_id or _already_tracked(db, msg_id):
            continue

        from_addr = msg.get("from", {}).get("emailAddress", {})
        sender = f"{from_addr.get('name', '')} <{from_addr.get('address', '')}>".strip()

        body_content = msg.get("body", {}).get("content", "")
        received_at_str = msg.get("receivedDateTime")
        received_at = _parse_graph_dt(received_at_str) or now

        row = TrackedEmail(
            graph_message_id=msg_id,
            graph_conversation_id=msg.get("conversationId", ""),
            email_type="quick_reply",
            # Status starts as 'pending_approval' but AI may change to skip
            status="pending_approval",
            original_subject=msg.get("subject", "(no subject)"),
            original_from=sender,
            original_to="me",
            original_body_preview=msg.get("bodyPreview", ""),
            original_body_html=body_content,
            sent_at=received_at,
        )
        db.add(row)
        new_rows.append(row)
        logger.info("Inbox email queued for AI review: %s", row.original_subject)
        if len(new_rows) >= 5:
            break

    _set_cursor(db, cursor_name, now)
    return new_rows
