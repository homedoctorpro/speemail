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
    return {
        "messages": messages,
        "has_more": bool(next_link_out),
        "next_link": quote(next_link_out, safe=""),
        "total": total,
        "top": top,
    }


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
