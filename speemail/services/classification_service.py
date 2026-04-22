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

# Sender-pattern thresholds
_PATTERN_MIN_SAMPLES = 3     # minimum feedback entries to start using the signal
_PATTERN_STRONG_SAMPLES = 5  # at this count + 0 replies → bypass Claude entirely

_CLASSIFY_SYSTEM = """\
You classify whether a received email requires the recipient to personally write a reply.

Return ONLY valid JSON in this exact format (no prose, no markdown fences):
{"needs_reply": true, "confidence": 0.85, "reasoning": "one sentence explanation"}

Confidence is 0.0–1.0. Reserve scores above 0.85 for cases where you are very certain.

If "Recipient name" is provided, use it to check salutations: if the email opens with "Hi [OtherName]" and that name does not match the recipient, treat it as likely intended for someone else (lower needs_reply confidence).

Addressing is an important signal but not definitive — always weigh it against content:
- Email sent directly to the recipient alone → strong signal they need to reply
- Email sent to the recipient and a few others → moderate signal
- Recipient is CC'd only → usually FYI, lower confidence they need to reply
- Recipient is not in To or CC → could be a distribution list, BCC, or alias —
  if the body clearly addresses them personally, treat it as a direct email;
  if it looks like a broadcast, lower confidence
- Addressing unknown → use content signals only

Emails that do NOT need a reply regardless of addressing:
- Automated receipts, invoices, order confirmations, payment notifications
- Shipping and delivery updates
- Password resets, verification codes, two-factor authentication codes
- Newsletters, marketing emails, promotional content
- System notifications and automated alerts
- Emails from addresses containing noreply, no-reply, donotreply, notifications, mailer
- Emails where the body says "you are receiving this because" or "do not reply"

Emails that DO need a reply (when addressed to the recipient):
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


def _addressing_label(msg: dict, user_email: str | None) -> str:
    """Describe how the email is addressed relative to the user."""
    if not user_email:
        return "unknown (user email not configured)"

    ue = user_email.lower()
    to_addrs = [r.get("emailAddress", {}).get("address", "").lower()
                for r in msg.get("toRecipients", [])]
    cc_addrs = [r.get("emailAddress", {}).get("address", "").lower()
                for r in msg.get("ccRecipients", [])]

    if ue in to_addrs:
        if len(to_addrs) == 1:
            return "sent directly to you and only you"
        return f"sent to you and {len(to_addrs) - 1} other(s)"
    if ue in cc_addrs:
        if to_addrs:
            return f"you are CC'd; email is addressed to {', '.join(to_addrs)}"
        return "you are CC'd only"
    if not to_addrs and not cc_addrs:
        return "no recipients listed (possible BCC, distribution list, or alias)"
    return f"you are not in To or CC (To: {', '.join(to_addrs) or 'empty'})"


_GENERIC_SALUTATIONS = {
    "all", "there", "team", "everyone", "folks", "guys", "friends",
    "sir", "madam", "whom", "it",
}


def _salutation_mismatch(body_preview: str, user_first_name: str) -> str | None:
    """
    If the email opens with 'Hi [Name]' and Name is not the user, return the greeted name.
    Returns None if there is no mismatch or not enough info to decide.
    """
    if not user_first_name or not body_preview:
        return None
    m = re.match(r"^(hi|hello|dear|hey),?\s+([a-z]+)", body_preview.strip(), re.IGNORECASE)
    if not m:
        return None
    greeted = m.group(2).lower()
    if greeted in _GENERIC_SALUTATIONS:
        return None
    if greeted == user_first_name.lower():
        return None
    return m.group(2)  # return original capitalisation for readable reasoning


def _get_sender_history(sender_address: str, db: Session) -> dict | None:
    """
    Returns reply/skip counts for a sender based on explicit user feedback.
    Returns None if there isn't enough history to draw conclusions.
    """
    if not sender_address:
        return None
    rows = (
        db.query(EmailFeedback)
        .filter(EmailFeedback.sender_address == sender_address.lower())
        .all()
    )
    if len(rows) < _PATTERN_MIN_SAMPLES:
        return None
    total = len(rows)
    replied = sum(1 for r in rows if r.decision == "needs_reply")
    skipped = total - replied
    return {"total": total, "replied": replied, "skipped": skipped,
            "skip_rate": skipped / total}


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _format_feedback(f: EmailFeedback) -> str:
    label = "NEEDS REPLY" if f.decision == "needs_reply" else "SKIP"
    reason = f" — {f.reason}" if f.reason else ""
    return f"[{label}{reason}] From: {f.sender_name} <{f.sender_address}> | Subject: {f.subject}"


def _build_classify_prompt(
    msg: dict,
    feedback: list[EmailFeedback],
    rules: str | None,
    user_email: str | None,
    sender_history: dict | None,
    user_name: str | None = None,
) -> str:
    parts: list[str] = []

    if rules:
        parts.append("Rules learned from this user's past decisions:")
        parts.append(rules)
        parts.append("")
        if feedback:
            parts.append("Recent examples:")
            for f in feedback[:5]:
                if f.decision != "resolved":
                    parts.append(_format_feedback(f))
            parts.append("")
    elif feedback:
        parts.append("Examples of past decisions by this user:")
        for f in feedback:
            if f.decision != "resolved":
                parts.append(_format_feedback(f))
        parts.append("")

    if sender_history:
        h = sender_history
        parts.append(
            f"Sender pattern: {h['total']} previous emails from this sender address in history. "
            f"User replied to {h['replied']}, skipped {h['skipped']} "
            f"({int(h['skip_rate'] * 100)}% skip rate)."
        )
        if h["replied"] == 0:
            parts.append(
                "The user has NEVER replied to this sender — treat this as a strong signal "
                "that emails from this address are transactional/bulk and do not need a reply."
            )
        elif h["skip_rate"] >= 0.7:
            parts.append(
                "The user rarely replies to this sender — likely a semi-automated source."
            )
        parts.append("")

    ea = msg.get("from", {}).get("emailAddress", {})
    parts.append("Email to classify:")
    if user_name:
        parts.append(f"Recipient name: {user_name}")
    parts.append(f"From: {ea.get('name', '')} <{ea.get('address', '')}>")
    parts.append(f"Addressing: {_addressing_label(msg, user_email)}")
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

    sender_address = msg.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    sender_history = _get_sender_history(sender_address, db)

    # Fast-path: sender has enough history and user has NEVER replied — skip Claude entirely.
    if (sender_history
            and sender_history["total"] >= _PATTERN_STRONG_SAMPLES
            and sender_history["replied"] == 0):
        reasoning = (
            f"Sender pattern: {sender_history['total']} previous emails from this address, "
            "user has never replied to any — treating as transactional."
        )
        logger.debug("Sender fast-path (never replied): %s", sender_address)
        _store_classification(db, msg_id, False, 0.05, reasoning)
        return {"needs_reply": False, "confidence": 0.05, "reasoning": reasoning}

    # Salutation mismatch fast-path: "Hi Sam" when user is not Sam → skip
    user_name_row = db.query(Setting).filter_by(key="user_name").first()
    user_name = user_name_row.value if user_name_row else None
    user_first_name = user_name.split()[0] if user_name else None
    body_preview = (msg.get("bodyPreview") or "")
    greeted_name = _salutation_mismatch(body_preview, user_first_name)
    if greeted_name:
        reasoning = f"Email is addressed to '{greeted_name}', not to you — likely intended for someone else."
        logger.debug("Salutation mismatch fast-path: greeted=%s, user=%s", greeted_name, user_first_name)
        _store_classification(db, msg_id, False, 0.10, reasoning)
        return {"needs_reply": False, "confidence": 0.10, "reasoning": reasoning}

    rules_row = db.query(Setting).filter_by(key=RULES_SETTING_KEY).first()
    rules = rules_row.value if rules_row else None

    user_email_row = db.query(Setting).filter_by(key="user_email").first()
    user_email = user_email_row.value if user_email_row else None

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
            messages=[{"role": "user", "content": _build_classify_prompt(
                msg, feedback, rules, user_email, sender_history, user_name
            )}],
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

    # Post-processing: cap confidence based on sender skip-rate history.
    if sender_history and needs_reply:
        h = sender_history
        if h["skip_rate"] >= 0.8:
            confidence = min(confidence, 0.30)
            reasoning += f" (capped: {h['skip_rate']:.0%} skip rate from {h['total']} prior emails)"
        elif h["skip_rate"] >= 0.6:
            confidence = min(confidence, 0.50)
            reasoning += f" (reduced: {h['skip_rate']:.0%} skip rate from {h['total']} prior emails)"

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

    # "resolved" decisions are not useful training signal — don't trigger rule derivation
    if decision == "resolved":
        db.commit()
        return

    count = db.query(EmailFeedback).count()
    if count >= 5 and count % DERIVE_AFTER_N_FEEDBACKS == 0:
        logger.info("Triggering background rule derivation at %d feedbacks", count)
        threading.Thread(target=_derive_rules_background, daemon=True).start()
