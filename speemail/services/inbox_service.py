"""
Inbox browsing helpers — thin wrappers around GraphClient for use in routes.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from speemail.auth.graph_auth import GraphClient
from speemail.services.ai_engine import html_to_text

logger = logging.getLogger(__name__)

# Matches `src="cid:..."` or `src='cid:...'` (Outlook-style inline image refs).
_CID_SRC_RE = re.compile(r"""src=(["'])cid:([^"']+)\1""", re.IGNORECASE)


def get_messages_page(client: GraphClient, folder: str = "Inbox", top: int = 30, next_link: str = "") -> dict:
    if next_link:
        data = client.get(next_link)
    else:
        data = client.list_messages(folder=folder, top=top, skip=0)
    messages = data.get("value", [])
    next_link_out = data.get("@odata.nextLink", "")
    total = data.get("@odata.count")

    # Group by conversationId — keep the most recent per thread (list is desc by receivedDateTime)
    seen: dict[str, int] = {}  # conversationId -> index in threaded list
    threaded: list[dict] = []
    for msg in messages:
        conv_id = msg.get("conversationId", "")
        if conv_id and conv_id in seen:
            threaded[seen[conv_id]]["_thread_count"] = threaded[seen[conv_id]].get("_thread_count", 1) + 1
        else:
            msg["_thread_count"] = 1
            if conv_id:
                seen[conv_id] = len(threaded)
            threaded.append(msg)

    return {
        "messages": threaded,
        "has_more": bool(next_link_out),
        "next_link": quote(next_link_out, safe=""),
        "total": total,
        "top": top,
    }


def get_message_detail(client: GraphClient, message_id: str) -> dict:
    msg = client.get_message(message_id)
    _inline_cid_images(client, msg)
    _attach_body_text(msg)
    return msg


def get_conversation_thread(client: GraphClient, conversation_id: str) -> list[dict]:
    """Return all messages in a conversation, oldest-first, with body text attached."""
    msgs = client.get_conversation_messages(conversation_id)
    for m in msgs:
        _inline_cid_images(client, m)
        _attach_body_text(m)
    return msgs


def _attach_body_text(msg: dict) -> None:
    body = msg.get("body", {})
    if body.get("contentType", "").lower() == "html":
        msg["body_text"] = html_to_text(body.get("content", ""))
    else:
        msg["body_text"] = body.get("content", "")


def _inline_cid_images(client: GraphClient, msg: dict) -> None:
    """
    Rewrite `<img src="cid:...">` references to inline data URIs so embedded
    images (Outlook signatures, forwarded screenshots) actually render. Browsers
    can't resolve cid: URLs on their own; we fetch the inline attachments from
    Graph and substitute their base64 bytes.

    Mutates msg['body']['content'] in place. No-op for non-HTML bodies or bodies
    with no cid references.
    """
    body = msg.get("body") or {}
    if body.get("contentType", "").lower() != "html":
        return
    content = body.get("content") or ""
    if "cid:" not in content.lower():
        return
    msg_id = msg.get("id")
    if not msg_id:
        return

    try:
        # Don't pass $select — Graph rejects it for certain attachment subtypes.
        data = client.get(f"/me/messages/{msg_id}/attachments")
    except Exception as exc:
        logger.warning("Failed to fetch attachments for %s: %s", msg_id, exc)
        return

    # contentId may arrive wrapped in angle brackets ("<abc@host>") — strip them.
    cid_map: dict[str, str] = {}
    for att in data.get("value", []):
        if not att.get("isInline"):
            continue
        cid = (att.get("contentId") or "").strip().strip("<>")
        if not cid:
            continue
        ct = att.get("contentType") or "image/png"
        bytes_b64 = att.get("contentBytes")
        if not bytes_b64:
            continue
        cid_map[cid.lower()] = f"data:{ct};base64,{bytes_b64}"

    if not cid_map:
        return

    def _replace(match: re.Match) -> str:
        quote_char = match.group(1)
        cid = match.group(2).strip().lower()
        # Some emails use "cid:image001.png@01DC..." — match on the full token first,
        # then on the part before the '@' as a fallback.
        data_url = cid_map.get(cid) or cid_map.get(cid.split("@", 1)[0])
        if data_url:
            return f"src={quote_char}{data_url}{quote_char}"
        return match.group(0)

    body["content"] = _CID_SRC_RE.sub(_replace, content)


def format_recipients(recipients: list[dict]) -> str:
    parts = []
    for r in recipients or []:
        ea = r.get("emailAddress", {})
        name = ea.get("name", "")
        addr = ea.get("address", "")
        parts.append(name or addr)
    return ", ".join(parts)
