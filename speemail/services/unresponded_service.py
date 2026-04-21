"""
Detects emails needing attention in both directions:
  - Needs Your Reply: inbox messages the user hasn't replied to
  - Awaiting Response: sent emails with no reply (from DB)
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import IgnoreRule, Setting, TrackedEmail

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_needs_reply_cache: dict = {"data": [], "ts": 0.0}



_AUTOMATED_SENDER_PATTERNS = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notification", "newsletter", "mailer", "postmaster",
    "bounce", "automated", "receipts@", "billing@",
    "updates@", "alerts@", "support@stripe", "stripe@",
    "invoices@", "statements@",
)

_AUTOMATED_SUBJECT_PATTERNS = (
    "receipt", "invoice", "order confirmation", "payment confirmation",
    "your order", "order #", "order number", "e-statement", "statement",
    "unsubscribe", "newsletter", "digest",
    "verify your email", "confirm your email", "email verification",
    "password reset", "reset your password",
    "verification code", "one-time code", "two-step verification", "2-step",
    "thanks for signing up", "welcome to ",
    "account notification", "account update", "security alert",
    "your subscription", "subscription confirmation", "auto-renewal",
    "shipment", "your package", "delivery",
    "you're receiving this", "you are receiving this",
)

_AUTOMATED_PREVIEW_PATTERNS = (
    "you're receiving this email because",
    "you are receiving this email because",
    "unsubscribe from this",
    "manage your preferences",
    "view it in your browser",
    "this is an automated",
    "do not reply to this",
    "partners with stripe",
)


def _is_automated_email(msg: dict) -> bool:
    """Return True if the email looks like an automated notification that won't need a reply."""
    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    subject = (msg.get("subject") or "").lower()
    preview = (msg.get("bodyPreview") or "").lower()

    for pattern in _AUTOMATED_SENDER_PATTERNS:
        if pattern in sender:
            return True
    for pattern in _AUTOMATED_SUBJECT_PATTERNS:
        if pattern in subject:
            return True
    for pattern in _AUTOMATED_PREVIEW_PATTERNS:
        if pattern in preview:
            return True
    return False


def _matches_ignore_rules(msg: dict, rules: list[IgnoreRule]) -> bool:
    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    subject = (msg.get("subject") or "").lower()
    for rule in rules:
        pattern = rule.pattern.lower()
        if rule.rule_type == "sender" and pattern in sender:
            return True
        if rule.rule_type == "subject" and pattern in subject:
            return True
    return False


def get_needs_reply(client: GraphClient, db: Session, limit: int = 20) -> list[dict]:
    """
    Return inbox messages the user has not replied to.
    Results are cached for 5 minutes; cache is invalidated when rules change.
    First scan uses full scan_days window; subsequent scans drop to 30 days.
    """
    now = time.monotonic()
    if now - _needs_reply_cache["ts"] < _CACHE_TTL:
        return _needs_reply_cache["data"][:limit]

    try:
        ignore_rules = db.query(IgnoreRule).all()
        result = _fetch_needs_reply(client, ignore_rules, limit)
        _needs_reply_cache["data"] = result
        _needs_reply_cache["ts"] = now

        return result
    except Exception as exc:
        logger.error("Failed to fetch unresponded inbox emails: %s", exc, exc_info=True)
        return _needs_reply_cache["data"][:limit]


def _fetch_needs_reply(
    client: GraphClient,
    ignore_rules: list[IgnoreRule],
    limit: int,
) -> list[dict]:
    # Fetch recent sent items: map conversationId → most recent sentDateTime
    sent_data = client.get(
        "/me/mailFolders/SentItems/messages",
        params={
            "$select": "conversationId,sentDateTime",
            "$top": "200",
        },
    )
    sent_conv_dates: dict[str, str] = {}
    for m in sent_data.get("value", []):
        conv_id = m.get("conversationId", "")
        sent_dt = m.get("sentDateTime", "")
        if conv_id not in sent_conv_dates or sent_dt > sent_conv_dates[conv_id]:
            sent_conv_dates[conv_id] = sent_dt

    # Fetch most recent inbox messages
    inbox_cap = min(limit * 5, 100)
    inbox_data = client.get(
        "/me/mailFolders/Inbox/messages",
        params={
            "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId,isRead",
            "$top": str(inbox_cap),
        },
    )

    logger.info("Sent items fetched: %d unique conv IDs", len(sent_conv_dates))
    inbox_msgs = inbox_data.get("value", [])
    logger.info("Inbox messages fetched: %d", len(inbox_msgs))

    unresponded = []
    for msg in inbox_msgs:
        conv_id = msg.get("conversationId", "")
        received_dt = msg.get("receivedDateTime", "")
        last_sent = sent_conv_dates.get(conv_id)
        if last_sent and last_sent > received_dt:
            continue
        if _is_automated_email(msg):
            logger.debug("Skipping automated email: %s", msg.get("subject"))
            continue
        if _matches_ignore_rules(msg, ignore_rules):
            continue
        unresponded.append(msg)
        if len(unresponded) >= limit:
            break

    logger.info("Unresponded found: %d", len(unresponded))
    return unresponded


def invalidate_cache() -> None:
    _needs_reply_cache["ts"] = 0.0


def get_awaiting_response(db: Session, limit: int = 20) -> list[TrackedEmail]:
    """Return sent follow-ups from DB that are awaiting a response."""
    return (
        db.query(TrackedEmail)
        .filter_by(email_type="follow_up", status="pending_approval")
        .order_by(TrackedEmail.sent_at.desc())
        .limit(limit)
        .all()
    )
