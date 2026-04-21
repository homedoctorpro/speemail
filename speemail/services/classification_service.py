"""
AI classification of inbox emails: does this message need a reply?

Uses Claude with user feedback as few-shot examples. Results are cached per
message in the DB so Claude is only called once per unseen email.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import anthropic
from sqlalchemy.orm import Session

from speemail.config import settings
from speemail.models.tables import EmailClassification, EmailFeedback

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
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


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _build_prompt(msg: dict, feedback: list[EmailFeedback]) -> str:
    parts: list[str] = []

    if feedback:
        parts.append("Examples of past decisions by this user:\n")
        for f in feedback:
            label = "NEEDS REPLY" if f.decision == "needs_reply" else "SKIP"
            reason = f" — reason: {f.reason}" if f.reason else ""
            parts.append(
                f"[{label}{reason}] "
                f"From: {f.sender_name} <{f.sender_address}> | "
                f"Subject: {f.subject}"
            )
        parts.append("")

    ea = msg.get("from", {}).get("emailAddress", {})
    parts.append("Email to classify:")
    parts.append(f"From: {ea.get('name', '')} <{ea.get('address', '')}>")
    parts.append(f"Subject: {msg.get('subject', '(no subject)')}")
    parts.append(f"Preview: {(msg.get('bodyPreview') or '')[:600]}")

    return "\n".join(parts)


def _store(db: Session, msg_id: str, needs_reply: bool, confidence: float, reasoning: str) -> None:
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

    feedback = (
        db.query(EmailFeedback)
        .order_by(EmailFeedback.created_at.desc())
        .limit(30)
        .all()
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(msg, feedback)}],
        )
        result = _parse(response.content[0].text)
        needs_reply = bool(result.get("needs_reply", False))
        confidence = float(result.get("confidence", 0.5))
        reasoning = str(result.get("reasoning", ""))
    except Exception as exc:
        logger.warning("Classification failed for %s: %s — defaulting to needs_reply=True", msg_id, exc)
        needs_reply = True
        confidence = 0.5
        reasoning = "Classification unavailable"

    _store(db, msg_id, needs_reply, confidence, reasoning)
    return {"needs_reply": needs_reply, "confidence": confidence, "reasoning": reasoning}


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
    """Save user feedback and update the classification cache to match."""
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
    _store(db, msg_id, needs_reply, 1.0, f"User decision: {decision}" + (f" — {reason}" if reason else ""))
