from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from speemail.api.deps import get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.services import inbox_service

router = APIRouter(tags=["inbox"])
logger = logging.getLogger(__name__)


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
    skip: int = Query(0),
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        data = inbox_service.get_messages_page(client, folder=folder, top=top, skip=skip)
    except Exception as exc:
        logger.error("Failed to fetch messages: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_message_list.html", data)


@router.get("/api/v1/inbox/messages/{message_id}", response_class=HTMLResponse)
def message_detail(
    message_id: str,
    request: Request,
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        msg = inbox_service.get_message_detail(client, message_id)
        # Mark as read silently
        if not msg.get("isRead"):
            try:
                client.mark_read(message_id)
            except Exception:
                pass
    except Exception as exc:
        logger.error("Failed to fetch message %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return _render(request, "partials/_message_detail.html", {"msg": msg})


@router.post("/api/v1/inbox/messages/{message_id}/reply", response_class=HTMLResponse)
def reply_message(
    message_id: str,
    request: Request,
    body: str = Form(...),
    client: GraphClient = Depends(get_graph_dep),
):
    try:
        client.reply_to_message(message_id, body_text=body)
    except Exception as exc:
        logger.error("Reply failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
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
    return _render(request, "partials/_toast.html", {"message": "Email sent!", "type": "success"})
