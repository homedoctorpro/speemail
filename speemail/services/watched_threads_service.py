"""Tracks outgoing emails the user is waiting for a reply on."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import Setting, WatchedThread  # noqa: F401 — re-exported for routes

logger = logging.getLogger(__name__)

_DEFAULT_ALERT_HOURS = 48


def get_alert_hours(db: Session) -> int:
    row = db.query(Setting).filter_by(key="watched_thread_alert_hours").first()
    try:
        return int(row.value) if row else _DEFAULT_ALERT_HOURS
    except (ValueError, TypeError):
        return _DEFAULT_ALERT_HOURS


def add(
    db: Session,
    graph_message_id: str,
    graph_conversation_id: str | None,
    subject: str,
    recipient: str,
    sent_at: datetime,
) -> WatchedThread:
    existing = db.query(WatchedThread).filter_by(graph_message_id=graph_message_id).first()
    if existing:
        return existing
    wt = WatchedThread(
        graph_message_id=graph_message_id,
        graph_conversation_id=graph_conversation_id or None,
        subject=subject,
        recipient=recipient,
        sent_at=sent_at,
    )
    db.add(wt)
    db.flush()
    return wt


def get_active(db: Session) -> list[WatchedThread]:
    return (
        db.query(WatchedThread)
        .filter_by(resolved=False, has_reply=False)
        .order_by(WatchedThread.sent_at.desc())
        .all()
    )


def resolve(db: Session, thread_id: int) -> None:
    wt = db.get(WatchedThread, thread_id)
    if wt:
        wt.resolved = True
        wt.resolved_at = datetime.utcnow()
        db.flush()


def check_replies(client: GraphClient, db: Session) -> int:
    """
    Check each active watched thread for a reply. Called from the scheduler.
    Returns the number of threads marked as replied.
    """
    active = (
        db.query(WatchedThread)
        .filter_by(resolved=False, has_reply=False)
        .filter(WatchedThread.graph_conversation_id.isnot(None))
        .all()
    )
    marked = 0
    for wt in active:
        try:
            if _has_reply(client, wt.graph_conversation_id, wt.sent_at):
                wt.has_reply = True
                wt.replied_at = datetime.utcnow()
                marked += 1
                logger.info("Reply detected for watched thread: %s", wt.subject)
        except Exception as exc:
            logger.warning("Reply check failed for watched thread %d: %s", wt.id, exc)
    return marked


def _has_reply(client: GraphClient, conversation_id: str, sent_at: datetime) -> bool:
    sent_str = sent_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    filter_q = (
        f"conversationId eq '{conversation_id}' "
        f"and receivedDateTime gt {sent_str}"
    )
    try:
        data = client.get(
            "/me/messages",
            params={"$filter": filter_q, "$select": "id", "$top": "1"},
        )
        return len(data.get("value", [])) > 0
    except Exception as exc:
        logger.warning("Thread reply check failed: %s", exc)
        return False


def is_overdue(wt: WatchedThread, alert_hours: int) -> bool:
    threshold = datetime.utcnow() - timedelta(hours=alert_hours)
    return wt.sent_at < threshold
