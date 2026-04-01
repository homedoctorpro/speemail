"""
Claude AI engine for drafting follow-up emails and quick replies.

All Claude calls are synchronous (run inside the APScheduler thread pool).
"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser

import anthropic

from speemail.config import settings
from speemail.models.tables import TrackedEmail

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_BODY_CHARS = 4000  # truncate long email bodies before sending to Claude


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(part.strip() for part in self._parts if part.strip())


def html_to_text(html: str) -> str:
    if not html:
        return ""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()[:MAX_BODY_CHARS]


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude's response, tolerating markdown code fences."""
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def _call_claude(client: anthropic.Anthropic, system: str, user: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


FOLLOW_UP_SYSTEM = """\
You are a professional email assistant. Your job is to draft polite, concise follow-up emails \
on behalf of the user. Write in a natural, non-pushy tone that matches the original email's style. \
Always respond with valid JSON only — no prose, no markdown fences."""

QUICK_REPLY_SYSTEM = """\
You are a professional email assistant. Your job is to assess whether an incoming email needs a \
quick reply, and if so, draft a concise response on behalf of the user. \
Always respond with valid JSON only — no prose, no markdown fences."""

CONFIDENCE_GUIDE = """\
Confidence scoring guide:
- 0.90–1.00: Simple, transactional context. Clear action required. High certainty.
- 0.70–0.89: Moderately clear. Might benefit from human review.
- 0.50–0.69: Ambiguous context or potentially sensitive. Human review recommended.
- Below 0.50: Missing context, complex relationship, or sensitive. Human must review."""


def draft_follow_up(email: TrackedEmail, user_display_name: str) -> dict | None:
    """
    Draft a follow-up for a sent email with no reply.
    Returns a dict with keys: subject, body, confidence, reasoning.
    Returns None on unrecoverable failure.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    body_text = html_to_text(email.original_body_html or email.original_body_preview or "")
    days = 0
    if email.sent_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        days = (now - email.sent_at).days

    user_prompt = f"""\
Draft a follow-up email for {user_display_name}.

ORIGINAL EMAIL (sent {days} day{"s" if days != 1 else ""} ago with no reply):
Subject: {email.original_subject}
To: {email.original_to or "unknown"}
Body:
{body_text or "(no body content)"}

TASK: Draft a polite follow-up. Requirements:
- 2–4 sentences maximum
- Reference the original topic naturally
- One clear, soft call-to-action
- Match the tone of the original

{CONFIDENCE_GUIDE}

Respond with this exact JSON structure:
{{
  "subject": "subject line (use Re: prefix if replying to same thread)",
  "body": "complete email body text",
  "confidence": 0.0,
  "reasoning": "brief explanation of the confidence score"
}}"""

    for attempt in range(2):
        try:
            raw = _call_claude(client, FOLLOW_UP_SYSTEM, user_prompt)
            result = _parse_json_response(raw)
            # Validate required keys
            if all(k in result for k in ("subject", "body", "confidence")):
                result.setdefault("reasoning", "")
                result["confidence"] = float(result["confidence"])
                return result
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Follow-up draft attempt %d failed to parse JSON: %s", attempt + 1, exc)
            if attempt == 0:
                user_prompt += "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the JSON object, nothing else."
        except anthropic.APIError as exc:
            logger.error("Claude API error drafting follow-up: %s", exc)
            return None

    logger.error("Failed to get valid JSON from Claude after 2 attempts")
    return None


def draft_quick_reply(email: TrackedEmail, user_display_name: str) -> dict | None:
    """
    Assess whether an incoming email needs a quick reply, and draft one if so.
    Returns a dict with keys: needs_quick_reply, subject, body, confidence, reasoning.
    Returns None on unrecoverable failure.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    body_text = html_to_text(email.original_body_html or email.original_body_preview or "")

    user_prompt = f"""\
Assess this incoming email for {user_display_name} and draft a reply if appropriate.

INCOMING EMAIL:
From: {email.original_from}
Subject: {email.original_subject}
Body:
{body_text or "(no body content)"}

TASK: First decide if this email needs a quick reply, then draft one if yes.

Emails that typically NEED a quick reply:
- Scheduling/meeting requests with clear options
- Simple yes/no questions
- Acknowledgment or confirmation requests
- Short factual questions you can answer without research

Emails to SKIP (set needs_quick_reply to false):
- Marketing emails or newsletters
- Automated notifications or receipts
- Complex multi-part questions requiring research or judgment
- Emotional, sensitive, or ambiguous situations
- Threads already mid-conversation (where context is missing)

{CONFIDENCE_GUIDE}

Respond with this exact JSON structure:
{{
  "needs_quick_reply": true,
  "skip_reason": "only set this if needs_quick_reply is false",
  "subject": "Re: subject line",
  "body": "complete reply body text",
  "confidence": 0.0,
  "reasoning": "brief explanation of the confidence score"
}}"""

    for attempt in range(2):
        try:
            raw = _call_claude(client, QUICK_REPLY_SYSTEM, user_prompt)
            result = _parse_json_response(raw)
            if "needs_quick_reply" in result:
                result.setdefault("reasoning", "")
                result.setdefault("skip_reason", "")
                result.setdefault("subject", f"Re: {email.original_subject}")
                result.setdefault("body", "")
                if "confidence" in result:
                    result["confidence"] = float(result["confidence"])
                else:
                    result["confidence"] = 0.0
                return result
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Quick reply attempt %d failed to parse JSON: %s", attempt + 1, exc)
            if attempt == 0:
                user_prompt += "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the JSON object, nothing else."
        except anthropic.APIError as exc:
            logger.error("Claude API error drafting quick reply: %s", exc)
            return None

    logger.error("Failed to get valid JSON from Claude after 2 attempts")
    return None


def apply_draft_to_email(email: TrackedEmail, draft: dict | None) -> None:
    """Write Claude's draft result back onto the TrackedEmail row (in-place)."""
    if draft is None:
        email.status = "ai_error"
        return

    email.ai_draft_subject = draft.get("subject", email.original_subject)
    email.ai_draft_body = draft.get("body", "")
    email.ai_confidence_score = draft.get("confidence", 0.0)
    email.ai_reasoning = draft.get("reasoning", "")

    # For quick replies, skip if AI says it doesn't need a reply
    if email.email_type == "quick_reply" and not draft.get("needs_quick_reply", True):
        email.status = "rejected"
        email.ai_reasoning = f"Auto-skipped: {draft.get('skip_reason', 'not needed')}"
