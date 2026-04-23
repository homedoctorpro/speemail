"""
Auto-generates Task rows from incoming emails that contain actionable work.

Called from classification_service.classify() when an email is classified as
needing a reply with high confidence. The dedup key is graph_message_id, so a
given email produces at most one task even if classify() runs again.
"""
from __future__ import annotations

import json
import logging
import re

import anthropic
from sqlalchemy.orm import Session

from speemail.config import settings
from speemail.models.tables import Task

logger = logging.getLogger(__name__)

# Minimum classifier confidence before we spend a Claude call on task extraction.
# Tasks are created only from emails we're confident need a response.
MIN_CONFIDENCE_FOR_EXTRACTION = 0.75

_SYSTEM = """\
You extract TODO items from emails. Given an email, decide whether it contains an \
actionable request that warrants creating a task for the recipient.

Return ONLY valid JSON (no prose, no markdown fences):
{"create_task": true, "title": "action-oriented title, max 80 chars", "priority": "high"}
or
{"create_task": false}

Create a task when:
- The sender explicitly asks the recipient to do something concrete (send X, gather Y, prepare Z, review, find)
- The email describes work the recipient must complete before they can reply meaningfully
- There is a deliverable the recipient needs to produce

Do NOT create a task when:
- The email only needs a short conversational reply
- It's a yes/no question answerable in one sentence
- It's informational/FYI with no action required
- It's purely a scheduling request (a calendar event, not a task)
- It's an automated notification, receipt, or confirmation

Title guidelines:
- Action-oriented, starts with a verb (Send, Gather, Review, Draft, Schedule, etc.)
- Include the sender's name or company when it clarifies context
- Keep under 80 characters

Priority:
- "high": urgent, time-sensitive, explicit deadline, or from a senior stakeholder
- "medium": normal business request (default)
- "low": nice-to-have, vague, or low stakes"""


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _first_text(response) -> str:
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def _build_prompt(msg: dict) -> str:
    ea = msg.get("from", {}).get("emailAddress", {})
    # Attacker-controlled fields are fenced so the extractor treats them as data.
    return (
        f"From: {ea.get('name', '')} <{ea.get('address', '')}>\n"
        "Subject:\n---BEGIN SUBJECT---\n"
        f"{msg.get('subject', '(no subject)')}\n"
        "---END SUBJECT---\n"
        "Preview:\n---BEGIN PREVIEW---\n"
        f"{(msg.get('bodyPreview') or '')[:800]}\n"
        "---END PREVIEW---"
    )


def maybe_create_task(msg: dict, db: Session, confidence: float) -> Task | None:
    """
    If the email warrants a task, create and return it. Otherwise return None.
    Safe to call multiple times for the same message — dedupes on graph_message_id.
    """
    if confidence < MIN_CONFIDENCE_FOR_EXTRACTION:
        return None

    msg_id = msg.get("id", "")
    if not msg_id:
        return None

    existing = db.query(Task).filter_by(source_graph_message_id=msg_id).first()
    if existing:
        return None

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_prompt(msg)}],
        )
        result = _parse(_first_text(response))
    except Exception as exc:
        logger.warning("Task extraction failed for %s: %s", msg_id, exc)
        return None

    if not result.get("create_task"):
        return None

    title = (result.get("title") or "").strip()
    if not title:
        return None
    title = title[:200]  # hard cap

    priority = result.get("priority", "medium")
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    ea = msg.get("from", {}).get("emailAddress", {})
    sender = ea.get("name") or ea.get("address", "")
    description = (
        f"Auto-generated from email by {sender}.\n\n"
        f"Subject: {msg.get('subject', '(no subject)')}\n\n"
        f"{(msg.get('bodyPreview') or '')[:500]}"
    )

    task = Task(
        title=title,
        description=description,
        status="todo",
        priority=priority,
        source_graph_message_id=msg_id,
    )
    db.add(task)
    db.commit()
    logger.info("Auto-created task from email %s: %s", msg_id, title)
    return task
