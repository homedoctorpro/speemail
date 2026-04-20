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
from speemail.models.tables import TrackedEmail

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes
_needs_reply_cache: dict = {"data": [], "ts": 0.0}


def get_needs_reply(client: GraphClient, limit: int = 20) -> list[dict]:
    """
    Return inbox messages the user has not replied to.
    Results are cached for 5 minutes to avoid hammering Graph API.
    """
    now = time.monotonic()
    if now - _needs_reply_cache["ts"] < _CACHE_TTL:
        return _needs_reply_cache["data"][:limit]

    try:
        result = _fetch_needs_reply(client, limit)
        _needs_reply_cache["data"] = result
        _needs_reply_cache["ts"] = now
        return result
    except Exception:
        logger.exception("Failed to fetch unresponded inbox emails")
        return _needs_reply_cache["data"][:limit]


def _fetch_needs_reply(client: GraphClient, limit: int) -> list[dict]:
    since_sent = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_inbox = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch recent sent items to build a set of replied-to conversation IDs
    sent_data = client.get(
        "/me/mailFolders/SentItems/messages",
        params={
            "$select": "conversationId,sentDateTime",
            "$filter": f"sentDateTime ge {since_sent}",
            "$top": "200",
        },
    )
    sent_conv_ids: set[str] = {m["conversationId"] for m in sent_data.get("value", [])}

    # Fetch recent inbox messages
    inbox_data = client.get(
        "/me/mailFolders/Inbox/messages",
        params={
            "$select": "id,subject,from,receivedDateTime,bodyPreview,conversationId,isRead",
            "$filter": f"receivedDateTime ge {since_inbox}",
            "$top": str(limit * 3),  # fetch extra to filter down
            "$orderby": "receivedDateTime desc",
        },
    )

    unresponded = []
    for msg in inbox_data.get("value", []):
        conv_id = msg.get("conversationId", "")
        if conv_id not in sent_conv_ids:
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
