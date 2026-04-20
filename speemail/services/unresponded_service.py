"""
Detects emails needing attention in both directions:
  - Needs Your Reply: inbox messages the user hasn't replied to
  - Awaiting Response: sent emails with no reply (from DB)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import IgnoreRule, Setting, TrackedEmail

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_needs_reply_cache: dict = {"data": [], "ts": 0.0}


def _get_scan_days(db: Session) -> int:
    row = db.query(Setting).filter_by(key="unresponded_scan_days").first()
    try:
        return max(1, int(row.value)) if row else 90
    except (ValueError, TypeError):
        return 90


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
    """
    now = time.monotonic()
    if now - _needs_reply_cache["ts"] < _CACHE_TTL:
        return _needs_reply_cache["data"][:limit]

    try:
        scan_days = _get_scan_days(db)
        ignore_rules = db.query(IgnoreRule).all()
        result = _fetch_needs_reply(client, scan_days, ignore_rules, limit)
        _needs_reply_cache["data"] = result
        _needs_reply_cache["ts"] = now
        return result
    except Exception:
        logger.exception("Failed to fetch unresponded inbox emails")
        return _needs_reply_cache["data"][:limit]


def _fetch_needs_reply(
    client: GraphClient,
    scan_days: int,
    ignore_rules: list[IgnoreRule],
    limit: int,
) -> list[dict]:
    since = (datetime.utcnow() - timedelta(days=scan_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent_since = (datetime.utcnow() - timedelta(days=scan_days + 30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch recent sent items to build a set of replied-to conversation IDs
    sent_data = client.get(
        "/me/mailFolders/SentItems/messages",
        params={
            "$select": "conversationId,sentDateTime",
            "$filter": f"sentDateTime ge {sent_since}",
            "$top": "500",
        },
    )
    sent_conv_ids: set[str] = {m["conversationId"] for m in sent_data.get("value", [])}

    # Paginate inbox messages up to a reasonable cap
    inbox_cap = min(limit * 10, 500)
    inbox_data = client.get(
        "/me/mailFolders/Inbox/messages",
        params={
            "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId,isRead",
            "$filter": f"receivedDateTime ge {since}",
            "$top": str(inbox_cap),
            "$orderby": "receivedDateTime desc",
        },
    )

    unresponded = []
    for msg in inbox_data.get("value", []):
        conv_id = msg.get("conversationId", "")
        if conv_id in sent_conv_ids:
            continue
        if _matches_ignore_rules(msg, ignore_rules):
            continue
        unresponded.append(msg)
        if len(unresponded) >= limit:
            break

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
