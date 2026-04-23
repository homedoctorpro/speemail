"""
Detects emails needing attention in both directions:
  - Needs Your Reply: inbox messages the user hasn't replied to
  - Awaiting Response: sent emails with no reply (from DB)
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from speemail.auth.graph_auth import GraphClient
from speemail.models.database import SessionLocal
from speemail.models.tables import EmailClassification, IgnoreRule, Setting, TrackedEmail
from speemail.services import classification_service

_DEFAULT_MIN_CONFIDENCE = 0.50

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes — controls background refresh frequency, not page load speed
# data=None means "never populated"; data=[] means "populated but empty"
_needs_reply_cache: dict = {"data": None, "ts": float("-inf"), "refreshing": False}



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
    """Fetch fresh data, update cache, and return results."""
    try:
        ignore_rules = db.query(IgnoreRule).all()
        result = _fetch_needs_reply(client, db, ignore_rules, limit)
        _needs_reply_cache["data"] = result
        _needs_reply_cache["ts"] = time.monotonic()
        return result
    except Exception as exc:
        logger.error("Failed to fetch unresponded inbox emails: %s", exc, exc_info=True)
        return (_needs_reply_cache["data"] or [])[:limit]
    finally:
        _needs_reply_cache["refreshing"] = False


def _refresh_in_background(client: GraphClient) -> None:
    """
    Kick off a one-shot background thread to refresh the cache.
    Opens its own DB session — the caller's request session is closed when
    the HTTP response returns, and SQLAlchemy Sessions aren't thread-safe.
    """
    if _needs_reply_cache["refreshing"]:
        return
    _needs_reply_cache["refreshing"] = True

    def _run() -> None:
        try:
            session = SessionLocal()
            try:
                get_needs_reply(client, session)
                logger.debug("Background needs-reply cache refresh complete")
            finally:
                session.close()
        except Exception as exc:
            logger.warning("Background needs-reply refresh failed: %s", exc)
        finally:
            _needs_reply_cache["refreshing"] = False

    threading.Thread(target=_run, daemon=True).start()


def get_needs_reply_cached(
    client: GraphClient,
    db: Session,
    limit: int = 10,
) -> list[dict] | None:
    """
    Return instantly from cache. Triggers a background refresh when stale.
    Returns None only on the very first load (cache never populated).
    """
    data = _needs_reply_cache["data"]
    if data is None:
        return None  # first ever load — caller falls back to blocking fetch
    if time.monotonic() - _needs_reply_cache["ts"] >= _CACHE_TTL:
        _refresh_in_background(client)
    return data[:limit]


def _get_min_confidence(db: Session) -> float:
    row = db.query(Setting).filter_by(key="needs_reply_min_confidence").first()
    if row:
        try:
            return int(row.value) / 100.0
        except (ValueError, TypeError):
            pass
    return _DEFAULT_MIN_CONFIDENCE


def _prewarm_classifications(msgs: list[dict]) -> None:
    """
    Run Claude classification in parallel for messages not yet in the DB cache.
    Each worker uses its own Session so SQLAlchemy thread-safety holds.
    Without this, first home load serializes ~30 Claude calls (~100s total).
    """
    if not msgs:
        return

    def _worker(msg: dict) -> None:
        session = SessionLocal()
        try:
            classification_service.classify(msg, session)
        except Exception as exc:
            logger.warning("Pre-warm classify failed for %s: %s", msg.get("id"), exc)
        finally:
            session.close()

    logger.info("Pre-warming %d classifications in parallel", len(msgs))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_worker, msgs))


def _fetch_needs_reply(
    client: GraphClient,
    db: Session,
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
            "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,bodyPreview,conversationId,isRead",
            "$top": str(inbox_cap),
        },
    )

    logger.info("Sent items fetched: %d unique conv IDs", len(sent_conv_dates))
    inbox_msgs = inbox_data.get("value", [])
    logger.info("Inbox messages fetched: %d", len(inbox_msgs))

    # Build a map of conversationId → all inbox messages in that thread, sorted oldest-first.
    # Used to detect when someone else has replied after the original email.
    conv_msgs: dict[str, list[dict]] = {}
    for m in inbox_msgs:
        cid = m.get("conversationId", "")
        conv_msgs.setdefault(cid, []).append(m)

    min_confidence = _get_min_confidence(db)

    # Pre-warm Claude classifications in parallel for messages that will reach
    # the AI step — skips anything already cached in DB or filtered by cheap
    # heuristics. Without this, the main loop's sequential classify() calls
    # make first-load take ~100s on a 50-message inbox.
    to_prewarm: list[dict] = []
    cached_ids: set[str] = {
        row.graph_message_id
        for row in db.query(EmailClassification.graph_message_id)
        .filter(EmailClassification.graph_message_id.in_([m.get("id", "") for m in inbox_msgs]))
        .all()
    }
    for msg in inbox_msgs:
        msg_id = msg.get("id", "")
        if not msg_id or msg_id in cached_ids:
            continue
        conv_id = msg.get("conversationId", "")
        received_dt = msg.get("receivedDateTime", "")
        last_sent = sent_conv_dates.get(conv_id)
        if last_sent and last_sent > received_dt:
            continue
        if _matches_ignore_rules(msg, ignore_rules):
            continue
        if _is_automated_email(msg):
            continue
        to_prewarm.append(msg)
    _prewarm_classifications(to_prewarm)

    unresponded = []
    for msg in inbox_msgs:
        conv_id = msg.get("conversationId", "")
        received_dt = msg.get("receivedDateTime", "")
        last_sent = sent_conv_dates.get(conv_id)
        if last_sent and last_sent > received_dt:
            continue
        if _matches_ignore_rules(msg, ignore_rules):
            continue

        # Fast heuristic: obvious automated emails skip Claude entirely
        if _is_automated_email(msg):
            logger.debug("Skipping automated email: %s", msg.get("subject"))
            continue

        # AI classification (cached in DB after first call)
        clf = classification_service.classify(msg, db)
        if not clf["needs_reply"] or clf["confidence"] < min_confidence:
            logger.debug(
                "Skipping (needs_reply=%s confidence=%.0f%% threshold=%.0f%%): %s",
                clf["needs_reply"],
                clf["confidence"] * 100,
                min_confidence * 100,
                msg.get("subject"),
            )
            continue

        # Detect other replies in the same thread that arrived after this message.
        original_sender = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
        thread_replies = [
            m for m in conv_msgs.get(conv_id, [])
            if m.get("receivedDateTime", "") > received_dt
            and m.get("from", {}).get("emailAddress", {}).get("address", "").lower() != original_sender
        ]
        if thread_replies:
            latest = max(thread_replies, key=lambda m: m.get("receivedDateTime", ""))
            latest_ea = latest.get("from", {}).get("emailAddress", {})
            msg["_thread_activity"] = {
                "count": len(thread_replies),
                "latest_sender": latest_ea.get("name") or latest_ea.get("address", "Someone"),
            }

        msg["_classification"] = clf
        unresponded.append(msg)
        if len(unresponded) >= limit:
            break

    logger.info("Unresponded found: %d", len(unresponded))
    return unresponded


def get_needs_reply_if_cached(limit: int = 10) -> list[dict] | None:
    """Return cached data only if fresh (no Graph calls). Used by inbox filter endpoint."""
    data = _needs_reply_cache["data"]
    if data is not None and time.monotonic() - _needs_reply_cache["ts"] < _CACHE_TTL:
        return data[:limit]
    return None


def invalidate_cache() -> None:
    _needs_reply_cache["ts"] = float("-inf")


def get_awaiting_response(db: Session, limit: int = 20) -> list[TrackedEmail]:
    """Return sent follow-ups from DB that are awaiting a response."""
    return (
        db.query(TrackedEmail)
        .filter_by(email_type="follow_up", status="pending_approval")
        .order_by(TrackedEmail.sent_at.desc())
        .limit(limit)
        .all()
    )
