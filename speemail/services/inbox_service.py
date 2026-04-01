"""
Inbox browsing helpers — thin wrappers around GraphClient for use in routes.
"""
from __future__ import annotations

from speemail.auth.graph_auth import GraphClient
from speemail.services.ai_engine import html_to_text


def get_messages_page(client: GraphClient, folder: str = "Inbox", top: int = 30, skip: int = 0) -> dict:
    data = client.list_messages(folder=folder, top=top, skip=skip)
    messages = data.get("value", [])
    has_more = bool(data.get("@odata.nextLink"))
    total = data.get("@odata.count")
    return {"messages": messages, "has_more": has_more, "total": total, "skip": skip, "top": top}


def get_message_detail(client: GraphClient, message_id: str) -> dict:
    msg = client.get_message(message_id)
    # Attach a plain-text version for display fallback
    body = msg.get("body", {})
    if body.get("contentType", "").lower() == "html":
        msg["body_text"] = html_to_text(body.get("content", ""))
    else:
        msg["body_text"] = body.get("content", "")
    return msg


def format_recipients(recipients: list[dict]) -> str:
    parts = []
    for r in recipients or []:
        ea = r.get("emailAddress", {})
        name = ea.get("name", "")
        addr = ea.get("address", "")
        parts.append(name or addr)
    return ", ".join(parts)
