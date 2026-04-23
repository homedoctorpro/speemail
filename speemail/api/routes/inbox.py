from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from datetime import datetime

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from sqlalchemy.orm import Session
from speemail.services import inbox_service, unresponded_service, watched_threads_service
from speemail.services import classification_service

router = APIRouter(tags=["inbox"])
logger = logging.getLogger(__name__)


def _get_message_states(messages: list[dict], db: Session) -> dict[str, str]:
    """
    Returns a map of message_id -> state string for color-coding.
    States: 'needs_reply' | 'watched' | 'awaiting' | ''
    """
    from speemail.models.tables import EmailClassification, EmailFeedback, WatchedThread

    # Conversation IDs of active watched threads
    watched_rows = db.query(WatchedThread).filter_by(resolved=False, has_reply=False).all()
    # Sent-thread watches (source != manual_inbox) → awaiting; inbox watches → watched
    awaiting_conv_ids = {
        w.graph_conversation_id for w in watched_rows
        if w.graph_conversation_id and w.source in ("auto", "manual_sent")
    }
    inbox_watched_conv_ids = {
        w.graph_conversation_id for w in watched_rows
        if w.graph_conversation_id and w.source == "manual_inbox"
    }

    # Message IDs explicitly marked needs_reply via feedback or classification
    msg_ids = [m.get("id", "") for m in messages]
    feedback_ids = {
        r.graph_message_id for r in
        db.query(EmailFeedback).filter(
            EmailFeedback.graph_message_id.in_(msg_ids),
            EmailFeedback.decision == "needs_reply"
        ).all()
    }
    clf_ids = {
        r.graph_message_id for r in
        db.query(EmailClassification).filter(
            EmailClassification.graph_message_id.in_(msg_ids),
            EmailClassification.needs_reply == True,  # noqa: E712
        ).all()
    }
    needs_reply_ids = feedback_ids | clf_ids

    states: dict[str, str] = {}
    for msg in messages:
        mid = msg.get("id", "")
        conv_id = msg.get("conversationId", "")
        if conv_id in awaiting_conv_ids:
            states[mid] = "awaiting"
        elif conv_id in inbox_watched_conv_ids:
            states[mid] = "watched"
        elif mid in needs_reply_ids:
            states[mid] = "needs_reply"
        else:
            states[mid] = ""
    return states


def _t(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, ctx: dict) -> HTMLResponse:
    ctx["request"] = request
    return HTMLResponse(_t(request).get_template(template).render(ctx))


# ── Pages ──────────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request):
    return _t(request).TemplateResponse("inbox.html", {"request": request})


# ── HTMX partials ──────────────────────────────────────────────────────────

@router.get("/api/v1/inbox/messages", response_class=HTMLResponse)
def list_messages(
    request: Request,
    folder: str = Query("Inbox"),
    top: int = Query(30),
    next_link: str = Query(""),
    client: GraphClient = Depends(get_graph_dep),
    db: Session = Depends(get_db_dep),
):
    try:
        data = inbox_service.get_messages_page(client, folder=folder, top=top, next_link=next_link)
    except Exception as exc:
        logger.error("Failed to fetch messages: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    data["message_states"] = _get_message_states(data["messages"], db)
    return _render(request, "partials/_message_list.html", data)


@router.get("/api/v1/inbox/filter/needs-reply", response_class=HTMLResponse)
def filter_needs_reply(
    request: Request,
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    # Use stale-while-revalidate cache — returns instantly even if data is old
    emails = unresponded_service.get_needs_reply_cached(client, db, limit=50) or []
    messages = [{"id": e["id"], "subject": e.get("subject"), "from": e.get("from"),
                 "receivedDateTime": e.get("receivedDateTime"), "bodyPreview": e.get("bodyPreview"),
                 "isRead": e.get("isRead", True), "hasAttachments": e.get("hasAttachments", False),
                 "conversationId": e.get("conversationId", ""),
                 "_classification": e.get("_classification"), "_thread_count": 1}
                for e in emails]
    states = {m["id"]: "needs_reply" for m in messages}
    return _render(request, "partials/_message_list.html",
                   {"messages": messages, "has_more": False, "next_link": "",
                    "message_states": states})


@router.get("/api/v1/inbox/filter/awaiting-response", response_class=HTMLResponse)
def filter_awaiting_response(
    request: Request,
    db: Session = Depends(get_db_dep),
):
    emails = unresponded_service.get_awaiting_response(db, limit=50)
    return _render(request, "partials/_awaiting_list.html", {"emails": emails})


@router.get("/api/v1/inbox/messages/{message_id}", response_class=HTMLResponse)
def message_detail(
    message_id: str,
    request: Request,
    client: GraphClient = Depends(get_graph_dep),
    db: Session = Depends(get_db_dep),
):
    try:
        msg = inbox_service.get_message_detail(client, message_id)
        if not msg.get("isRead"):
            try:
                client.mark_read(message_id)
            except Exception:
                pass
    except Exception as exc:
        logger.error("Failed to fetch message %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    # Fetch full conversation thread
    conv_id = msg.get("conversationId", "")
    try:
        thread_msgs = inbox_service.get_conversation_thread(client, conv_id) if conv_id else [msg]
    except Exception:
        thread_msgs = [msg]

    # Check if this conversation is being watched
    from speemail.models.tables import WatchedThread
    watched = (
        db.query(WatchedThread)
        .filter_by(resolved=False, has_reply=False)
        .filter(WatchedThread.graph_conversation_id == conv_id)
        .first()
    ) if conv_id else None

    return _render(request, "partials/_message_detail.html", {
        "msg": msg,
        "thread_msgs": thread_msgs,
        "watched": watched,
    })


@router.post("/api/v1/inbox/messages/{message_id}/reply", response_class=HTMLResponse)
def reply_message(
    message_id: str,
    request: Request,
    body: str = Form(...),
    watch: str = Form(default=""),
    subject: str = Form(default=""),
    sender_address: str = Form(default=""),
    sender_name: str = Form(default=""),
    body_preview: str = Form(default=""),
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        draft = client.reply_to_message(message_id, body_text=body)
    except Exception as exc:
        logger.error("Reply failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    # Sending a reply resolves the "needs your reply" classification
    try:
        classification_service.record_feedback(
            db, message_id, "resolved", None,
            subject, sender_address, sender_name, body_preview,
        )
        db.commit()
        unresponded_service.invalidate_cache()
    except Exception as exc:
        logger.warning("Failed to record reply feedback for %s: %s", message_id, exc)

    if watch == "true":
        try:
            msg = inbox_service.get_message_detail(client, message_id)
            watched_threads_service.add(
                db=db,
                graph_message_id=draft["id"],
                graph_conversation_id=draft.get("conversationId"),
                subject=msg.get("subject", "(no subject)"),
                recipient=msg.get("from", {}).get("emailAddress", {}).get("name")
                          or msg.get("from", {}).get("emailAddress", {}).get("address", ""),
                sent_at=datetime.utcnow(),
            )
            db.commit()
        except Exception as exc:
            logger.warning("Failed to watch thread after reply: %s", exc)

    return _render(request, "partials/_toast.html", {"message": "Reply sent!", "type": "success"})


@router.post("/api/v1/inbox/messages/{message_id}/forward", response_class=HTMLResponse)
def forward_message(
    message_id: str,
    request: Request,
    to: str = Form(...),
    body: str = Form(default=""),
    client: GraphClient = Depends(get_graph_dep),
):
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="At least one recipient required")
    try:
        client.forward_message(message_id, to=recipients, body_text=body)
    except Exception as exc:
        logger.error("Forward failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_toast.html", {"message": "Forwarded!", "type": "success"})


@router.post("/api/v1/inbox/messages/{message_id}/trash", response_class=HTMLResponse)
def trash_message(
    message_id: str,
    request: Request,
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        client.move_to_trash(message_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_toast.html", {"message": "Moved to trash.", "type": "success"})


@router.get("/partials/compose", response_class=HTMLResponse)
def compose_partial(request: Request):
    return _render(request, "partials/_compose_modal.html", {})


@router.get("/partials/reply/{message_id}", response_class=HTMLResponse)
def reply_partial(
    message_id: str,
    request: Request,
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        msg = inbox_service.get_message_detail(client, message_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_reply_modal.html", {"msg": msg})


@router.get("/partials/forward/{message_id}", response_class=HTMLResponse)
def forward_partial(
    message_id: str,
    request: Request,
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        msg = inbox_service.get_message_detail(client, message_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_forward_modal.html", {"msg": msg})


@router.post("/api/v1/compose", response_class=HTMLResponse)
def compose_send(
    request: Request,
    to: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    watch: str = Form(default=""),
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="At least one recipient required")
    try:
        client.send_new_email(to=recipients, subject=subject, body=body)
    except Exception as exc:
        logger.error("Compose send failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    if watch == "true":
        try:
            sent = client.get_latest_sent_message()
            if sent:
                watched_threads_service.add(
                    db=db,
                    graph_message_id=sent["id"],
                    graph_conversation_id=sent.get("conversationId"),
                    subject=subject,
                    recipient=recipients[0],
                    sent_at=datetime.utcnow(),
                )
                db.commit()
        except Exception as exc:
            logger.warning("Failed to watch thread after compose: %s", exc)

    return _render(request, "partials/_toast.html", {"message": "Email sent!", "type": "success"})
