from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import TrackedEmail
from speemail.services.email_sender import SendError, send_reply

router = APIRouter(prefix="/api/v1/emails", tags=["emails"])
logger = logging.getLogger(__name__)


def _templates(request: Request):
    return request.app.state.templates


def _render_card(request: Request, email: TrackedEmail) -> HTMLResponse:
    t = _templates(request)
    return HTMLResponse(
        t.get_template("partials/_email_card.html").render(
            {"request": request, "email": email}
        )
    )


def _render_card_list(request: Request, emails: list[TrackedEmail]) -> HTMLResponse:
    t = _templates(request)
    return HTMLResponse(
        t.get_template("partials/_email_card_list.html").render(
            {"request": request, "emails": emails}
        )
    )


@router.get("/pending", response_class=HTMLResponse)
def list_pending(request: Request, db: Session = Depends(get_db_dep)):
    emails = (
        db.query(TrackedEmail)
        .filter(TrackedEmail.status == "pending_approval")
        .order_by(TrackedEmail.created_at.desc())
        .all()
    )
    return _render_card_list(request, emails)


@router.post("/{email_id}/approve", response_class=HTMLResponse)
def approve(
    email_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    email = db.get(TrackedEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.status == "sent":
        return _render_card(request, email)

    try:
        send_reply(client, db, email)
    except SendError as exc:
        logger.error("Send failed for email %d: %s", email_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return _render_card(request, email)


@router.post("/{email_id}/approve-edited", response_class=HTMLResponse)
def approve_edited(
    email_id: int,
    request: Request,
    edited_body: str = Form(...),
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    email = db.get(TrackedEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.status == "sent":
        return _render_card(request, email)

    email.user_edited_body = edited_body

    try:
        send_reply(client, db, email)
    except SendError as exc:
        logger.error("Send failed for email %d: %s", email_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return _render_card(request, email)


@router.post("/{email_id}/reject", response_class=HTMLResponse)
def reject(
    email_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    email = db.get(TrackedEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    email.status = "rejected"
    return _render_card(request, email)


@router.get("/{email_id}/draft", response_class=HTMLResponse)
def get_draft(
    email_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    email = db.get(TrackedEmail, email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    t = _templates(request)
    return HTMLResponse(
        t.get_template("partials/_edit_modal.html").render(
            {"request": request, "email": email}
        )
    )


@router.get("/history", response_class=HTMLResponse)
def email_history(request: Request, db: Session = Depends(get_db_dep)):
    emails = (
        db.query(TrackedEmail)
        .filter(TrackedEmail.status.in_(["sent", "auto_sent", "rejected", "ai_error"]))
        .order_by(TrackedEmail.updated_at.desc())
        .limit(100)
        .all()
    )
    t = _templates(request)
    return HTMLResponse(
        t.get_template("partials/_history_list.html").render(
            {"request": request, "emails": emails}
        )
    )
