"""
Inbox browsing helpers — thin wrappers around GraphClient for use in routes.
"""
from __future__ import annotations

from urllib.parse import quote

from speemail.auth.graph_auth import GraphClient
from speemail.services.ai_engine import html_to_text


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
    _attach_body_text(msg)
    return msg


def get_conversation_thread(client: GraphClient, conversation_id: str) -> list[dict]:
    """Return all messages in a conversation, oldest-first, with body text attached."""
    msgs = client.get_conversation_messages(conversation_id)
    for m in msgs:
        _attach_body_text(m)
    return msgs


def _attach_body_text(msg: dict) -> None:
    body = msg.get("body", {})
    if body.get("contentType", "").lower() == "html":
        msg["body_text"] = html_to_text(body.get("content", ""))
    else:
        msg["body_text"] = body.get("content", "")


def format_recipients(recipients: list[dict]) -> str:
    parts = []
    for r in recipients or []:
        ea = r.get("emailAddress", {})
        name = ea.get("name", "")
        addr = ea.get("address", "")
        parts.append(name or addr)
    return ", ".join(parts)
