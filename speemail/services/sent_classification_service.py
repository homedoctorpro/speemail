"""
AI classification of sent emails: does this message expect a reply?

Same two-stage learning pattern as classification_service:
  1. Few-shot: raw feedback examples passed to Claude
  2. Rule derivation: after every 10 feedbacks, Claude synthesises rules
"""
from __future__ import annotations

import json
import logging
import re
import threading

import anthropic
from sqlalchemy.orm import Session

from speemail.config import settings
from speemail.models.tables import SentEmailScan, Setting, WatchedThread

logger = logging.getLogger(__name__)

RULES_SETTING_KEY = "sent_classification_rules"
DERIVE_AFTER_N_FEEDBACKS = 10
_DEFAULT_MIN_CONFIDENCE = 0.60

_CLASSIFY_SYSTEM = """\
You classify whether a sent email is likely to expect a reply from the recipient.

Return ONLY valid JSON (no prose, no markdown fences):
{"expects_reply": true, "confidence": 0.85, "reasoning": "one sentence explanation"}

Confidence is 0.0–1.0. Reserve scores above 0.85 for cases where you are very certain.

Emails that DO expect a reply:
- Emails asking a direct question
- Emails requesting information, feedback, review, or approval
- Emails proposing a meeting or requesting scheduling input
- Emails sending a document/proposal and asking for thoughts
- Emails following up on a previous unanswered request

Emails that do NOT expect a reply:
- FYI / informational emails with no question or request
- Thank-you or acknowledgment emails ("Thanks, got it")
- One-way announcements or notifications
- Emails that explicitly say "no need to reply" or "just keeping you posted"
- Auto-generated or templated emails"""

_DERIVE_SYSTEM = """\
You analyze sent email classification decisions and extract reusable rules.
Write 5-10 concise bullet points (starting with •) that capture the specific patterns
in this user's decisions about which sent emails expect a reply. Focus on patterns
specific to this user — skip obvious defaults. Include both what expects a reply
and what doesn't. If the user gave reasons, use them to make rules more precise."""


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _first_text(response) -> str:
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def _format_feedback(scan: SentEmailScan) -> str:
    label = "EXPECTS REPLY" if scan.user_decision == "expects_reply" else "NO REPLY EXPECTED"
    return f"[{label}] To: {scan.recipient} | Subject: {scan.subject}"


def _build_prompt(
    msg: dict,
    feedback: list[SentEmailScan],
    rules: str | None,
) -> str:
    parts: list[str] = []

    if rules:
        parts.append("Rules learned from this user's past decisions:")
        parts.append(rules)
        parts.append("")
        if feedback:
            parts.append("Recent examples:")
            for f in feedback[:5]:
                parts.append(_format_feedback(f))
            parts.append("")
    elif feedback:
        parts.append("Examples of past decisions by this user:")
        for f in feedback:
            parts.append(_format_feedback(f))
        parts.append("")

    to_addrs = ", ".join(
        r.get("emailAddress", {}).get("address", "")
        for r in msg.get("toRecipients", [])
    )
    parts.append("Sent email to classify:")
    parts.append(f"To: {to_addrs}")
    # Attacker-controlled fields fenced as data, not instructions.
    parts.append("Subject:")
    parts.append("---BEGIN SUBJECT---")
    parts.append(msg.get("subject", "(no subject)"))
    parts.append("---END SUBJECT---")
    parts.append("Preview:")
    parts.append("---BEGIN PREVIEW---")
    parts.append((msg.get("bodyPreview") or "")[:600])
    parts.append("---END PREVIEW---")
    return "\n".join(parts)


def classify_sent(msg: dict, db: Session) -> dict:
    """
    Return {'expects_reply': bool, 'confidence': float, 'reasoning': str}.
    Reads from DB cache first; calls Claude on cache miss.
    """
    msg_id = msg.get("id", "")
    cached = db.query(SentEmailScan).filter_by(graph_message_id=msg_id).first()
    if cached:
        return {
            "expects_reply": cached.expects_reply,
            "confidence": cached.confidence,
            "reasoning": cached.reasoning,
        }

    rules_row = db.query(Setting).filter_by(key=RULES_SETTING_KEY).first()
    rules = rules_row.value if rules_row else None
    example_limit = 5 if rules else 30
    feedback = (
        db.query(SentEmailScan)
        .filter(SentEmailScan.user_decision.isnot(None))
        .order_by(SentEmailScan.scanned_at.desc())
        .limit(example_limit)
        .all()
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": _build_prompt(msg, feedback, rules)}],
        )
        result = _parse(_first_text(response))
        expects_reply = bool(result.get("expects_reply", False))
        confidence = float(result.get("confidence", 0.5))
        reasoning = str(result.get("reasoning", ""))
    except Exception as exc:
        logger.warning("Sent classification failed for %s: %s — defaulting expects_reply=False", msg_id, exc)
        expects_reply = False
        confidence = 0.5
        reasoning = "Classification unavailable"

    to_addrs = ", ".join(
        r.get("emailAddress", {}).get("name") or r.get("emailAddress", {}).get("address", "")
        for r in msg.get("toRecipients", [])
    )
    scan = SentEmailScan(
        graph_message_id=msg_id,
        subject=msg.get("subject", ""),
        recipient=to_addrs,
        body_preview=(msg.get("bodyPreview") or "")[:500],
        expects_reply=expects_reply,
        confidence=confidence,
        reasoning=reasoning,
    )
    db.add(scan)
    db.commit()
    return {"expects_reply": expects_reply, "confidence": confidence, "reasoning": reasoning}


def record_feedback(db: Session, msg_id: str, decision: str) -> None:
    """Record user feedback (decision: 'expects_reply' | 'skip') on a sent email scan."""
    scan = db.query(SentEmailScan).filter_by(graph_message_id=msg_id).first()
    if not scan:
        return
    scan.user_decision = decision
    db.commit()

    count = db.query(SentEmailScan).filter(SentEmailScan.user_decision.isnot(None)).count()
    if count >= 5 and count % DERIVE_AFTER_N_FEEDBACKS == 0:
        threading.Thread(target=_derive_rules_background, daemon=True).start()


def derive_rules(db: Session) -> str | None:
    feedback = (
        db.query(SentEmailScan)
        .filter(SentEmailScan.user_decision.isnot(None))
        .order_by(SentEmailScan.scanned_at.asc())
        .all()
    )
    if len(feedback) < 5:
        return None

    examples = "\n".join(_format_feedback(f) for f in feedback)
    prompt = f"Feedback decisions:\n{examples}\n\nDerive rules from these decisions."

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=_DERIVE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        rules_text = _first_text(response).strip()
    except Exception as exc:
        logger.warning("Sent rule derivation failed: %s", exc)
        return None

    existing = db.query(Setting).filter_by(key=RULES_SETTING_KEY).first()
    if existing:
        existing.value = rules_text
    else:
        db.add(Setting(key=RULES_SETTING_KEY, value=rules_text))
    db.commit()
    logger.info("Sent classification rules derived from %d feedback examples", len(feedback))
    return rules_text


def _derive_rules_background() -> None:
    from speemail.models.database import get_session
    try:
        with get_session() as db:
            derive_rules(db)
    except Exception as exc:
        logger.warning("Background sent rule derivation failed: %s", exc)


def scan_sent_items(
    client_graph,
    db: Session,
    msgs: list[dict],
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> int:
    """
    Classify a batch of sent messages and create WatchedThread entries for those
    that expect a reply and haven't been scanned before. Returns count of new watches.
    """
    from speemail.services.watched_threads_service import add as add_watch
    from speemail.services.email_poller import _thread_has_reply, _parse_graph_dt

    user_email_row = db.query(Setting).filter_by(key="user_email").first()
    user_email = user_email_row.value if user_email_row else None
    watched = 0
    for msg in msgs:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue

        # Already watched (auto or manual) — don't create a duplicate
        if db.query(WatchedThread).filter_by(graph_message_id=msg_id).first():
            continue

        # classify_sent() reads from the scan cache internally, so this is
        # cheap for already-classified messages. We must still reach the
        # watch-creation step for cached classifications that never got
        # a watch (e.g. after bug fixes to the watch-creation logic).
        clf = classify_sent(msg, db)

        if not clf["expects_reply"] or clf["confidence"] < min_confidence:
            logger.debug(
                "Sent email not watched (expects_reply=%s conf=%.0f%%): %s",
                clf["expects_reply"], clf["confidence"] * 100, msg.get("subject"),
            )
            continue

        # Check if a reply already exists before creating a watch
        conv_id = msg.get("conversationId", "")
        sent_at = _parse_graph_dt(msg.get("sentDateTime")) or __import__("datetime").datetime.utcnow()
        if conv_id and _thread_has_reply(client_graph, conv_id, sent_at, user_email):
            logger.debug("Reply already exists for sent email, skipping watch: %s", msg.get("subject"))
            continue

        to_addrs = ", ".join(
            r.get("emailAddress", {}).get("name") or r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        )
        wt = add_watch(
            db=db,
            graph_message_id=msg_id,
            graph_conversation_id=conv_id or None,
            subject=msg.get("subject", "(no subject)"),
            recipient=to_addrs,
            sent_at=sent_at,
        )
        wt.source = "auto"
        wt.ai_expects_reply = clf["expects_reply"]
        wt.ai_confidence = clf["confidence"]
        wt.ai_reasoning = clf["reasoning"]
        db.commit()
        watched += 1
        logger.info("Auto-watching sent email: %s", msg.get("subject"))

    return watched
