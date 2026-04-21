"""
AI classification of inbox emails: does this message need a reply?

Two-stage learning system:
  1. Few-shot: passes raw feedback examples to Claude (works from first feedback)
  2. Rule derivation: after every 10 feedbacks, Claude synthesizes the examples
     into concise rules stored in the settings table. Rules replace raw examples
     in the prompt, so the system keeps improving beyond the few-shot ceiling.

Results are cached per message in email_classifications so Claude is only
called once per unseen email.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime

import anthropic
from sqlalchemy.orm import Session

from speemail.config import settings
from speemail.models.tables import EmailClassification, EmailFeedback, Setting

logger = logging.getLogger(__name__)

RULES_SETTING_KEY = "classification_rules"
DERIVE_AFTER_N_FEEDBACKS = 10  # re-derive rules every N feedbacks

_CLASSIFY_SYSTEM = """\
You classify whether a received email requires the recipient to personally write a reply.

Return ONLY valid JSON in this exact format (no prose, no markdown fences):
{"needs_reply": true, "confidence": 0.85, "reasoning": "one sentence explanation"}

Confidence is 0.0–1.0. Reserve scores above 0.85 for cases where you are very certain.

Emails that do NOT need a reply:
- Automated receipts, invoices, order confirmations, payment notifications
- Shipping and delivery updates
- Password resets, verification codes, two-factor authentication codes
- Newsletters, marketing emails, promotional content
- System notifications and automated alerts
- Emails from addresses containing noreply, no-reply, donotreply, notifications, mailer
- Emails where the body says "you are receiving this because" or "do not reply"

Emails that DO need a reply:
- Personal emails from real people asking a direct question
- Meeting requests or scheduling emails requiring a response
- Emails from colleagues, clients, or partners expecting an answer
- Anything where not replying would be rude or leave someone waiting
- Follow-up emails asking if you received or reviewed something"""

_DERIVE_SYSTEM = """\
You analyze email classification decisions and extract reusable rules.
Write 5-10 concise bullet points (starting with •) that capture the specific patterns
in this user's decisions. Focus on patterns that are specific to this user — skip
obvious defaults like "receipts don't need replies". Include both what needs a reply
and what to skip. If the user gave reasons, use them to make rules more precise."""


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _format_feedback(f: EmailFeedback) -> str:
    label = "NEEDS REPLY" if f.decision == "needs_reply" else "SKIP"
    reason = f" — {f.reason}" if f.reason else ""
    return f"[{label}{reason}] From: {f.sender_name} <{f.sender_address}> | Subject: {f.subject}"


def _build_classify_prompt(msg: dict, feedback: list[EmailFeedback], rules: str | None) -> str:
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

    ea = msg.get("from", {}).get("emailAddress", {})
    parts.append("Email to classify:")
    parts.append(f"From: {ea.get('name', '')} <{ea.get('address', '')}>")
    parts.append(f"Subject: {msg.get('subject', '(no subject)')}")
    parts.append(f"Preview: {(msg.get('bodyPreview') or '')[:600]}")
    return "\n".join(parts)


def _store_classification(db: Session, msg_id: str, needs_reply: bool, confidence: float, reasoning: str) -> None:
    existing = db.query(EmailClassification).filter_by(graph_message_id=msg_id).first()
    if existing:
        existing.needs_reply = needs_reply
        existing.confidence = confidence
        existing.reasoning = reasoning
        existing.classified_at = datetime.utcnow()
    else:
        db.add(EmailClassification(
            graph_message_id=msg_id,
            needs_reply=needs_reply,
            confidence=confidence,
            reasoning=reasoning,
        ))
    db.commit()


def classify(msg: dict, db: Session) -> dict:
    """
    Return {'needs_reply': bool, 'confidence': float, 'reasoning': str}.
    Reads from DB cache first; calls Claude on cache miss.
    """
    msg_id = msg.get("id", "")

    cached = db.query(EmailClassification).filter_by(graph_message_id=msg_id).first()
    if cached:
        return {
            "needs_reply": cached.needs_reply,
            "confidence": cached.confidence,
            "reasoning": cached.reasoning,
        }

    rules_row = db.query(Setting).filter_by(key=RULES_SETTING_KEY).first()
    rules = rules_row.value if rules_row else None

    # Fewer raw examples needed once rules exist
    example_limit = 5 if rules else 30
    feedback = (
        db.query(EmailFeedback)
        .order_by(EmailFeedback.created_at.desc())
        .limit(example_limit)
        .all()
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": _build_classify_prompt(msg, feedback, rules)}],
        )
        result = _parse(response.content[0].text)
        needs_reply = bool(result.get("needs_reply", False))
        confidence = float(result.get("confidence", 0.5))
        reasoning = str(result.get("reasoning", ""))
    except Exception as exc:
        logger.warning("Classification failed for %s: %s — defaulting needs_reply=True", msg_id, exc)
        needs_reply = True
        confidence = 0.5
        reasoning = "Classification unavailable"

    _store_classification(db, msg_id, needs_reply, confidence, reasoning)
    return {"needs_reply": needs_reply, "confidence": confidence, "reasoning": reasoning}


# ── Rule derivation ───────────────────────────────────────────────────────────

def derive_rules(db: Session) -> str | None:
    """
    Ask Claude to synthesize all feedback into concise rules.
    Stores the result in the settings table and returns the rules text.
    Only runs if there are at least 5 feedback decisions.
    """
    feedback = db.query(EmailFeedback).order_by(EmailFeedback.created_at.asc()).all()
    if len(feedback) < 5:
        logger.info("Not enough feedback to derive rules yet (%d)", len(feedback))
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
        rules_text = response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Rule derivation failed: %s", exc)
        return None

    existing = db.query(Setting).filter_by(key=RULES_SETTING_KEY).first()
    if existing:
        existing.value = rules_text
    else:
        db.add(Setting(key=RULES_SETTING_KEY, value=rules_text))
    db.commit()

    logger.info("Classification rules derived from %d feedback examples", len(feedback))
    return rules_text


def _derive_rules_background() -> None:
    """Spawn a background thread with its own DB session to derive rules."""
    from speemail.models.database import get_session
    try:
        with get_session() as db:
            derive_rules(db)
    except Exception as exc:
        logger.warning("Background rule derivation failed: %s", exc)


def record_feedback(
    db: Session,
    msg_id: str,
    decision: str,
    reason: str | None,
    subject: str,
    sender_address: str,
    sender_name: str,
    body_preview: str,
) -> None:
    """
    Save user feedback and update the classification cache to match.
    Triggers background rule derivation every DERIVE_AFTER_N_FEEDBACKS feedbacks.
    """
    existing_fb = db.query(EmailFeedback).filter_by(graph_message_id=msg_id).first()
    if existing_fb:
        existing_fb.decision = decision
        existing_fb.reason = reason or None
    else:
        db.add(EmailFeedback(
            graph_message_id=msg_id,
            subject=subject,
            sender_address=sender_address,
            sender_name=sender_name,
            body_preview=body_preview,
            decision=decision,
            reason=reason or None,
        ))

    needs_reply = decision == "needs_reply"
    _store_classification(db, msg_id, needs_reply, 1.0,
                          f"User decision: {decision}" + (f" — {reason}" if reason else ""))

    count = db.query(EmailFeedback).count()
    if count >= 5 and count % DERIVE_AFTER_N_FEEDBACKS == 0:
        logger.info("Triggering background rule derivation at %d feedbacks", count)
        threading.Thread(target=_derive_rules_background, daemon=True).start()
