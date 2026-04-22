from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.services import sent_classification_service, watched_threads_service

router = APIRouter(tags=["watched_threads"])


@router.delete("/api/v1/watched-threads/{thread_id}", response_class=HTMLResponse)
def resolve_watched_thread(
    thread_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
):
    watched_threads_service.resolve(db, thread_id)
    db.commit()
    return request.app.state.templates.TemplateResponse(
        "partials/_toast.html",
        {"request": request, "message": "Marked as resolved.", "type": "success"},
    )


@router.post("/api/v1/watched-threads/{thread_id}/feedback", response_class=HTMLResponse)
def watched_thread_feedback(
    thread_id: int,
    request: Request,
    decision: str = Form(...),
    db: Session = Depends(get_db_dep),
):
    """Record feedback on an auto-classified watched thread (expects_reply | skip)."""
    wt = db.get(watched_threads_service.WatchedThread, thread_id)
    if wt and wt.source == "auto":
        sent_classification_service.record_feedback(db, wt.graph_message_id, decision)
        if decision == "skip":
            watched_threads_service.resolve(db, thread_id)
            db.commit()
            return request.app.state.templates.TemplateResponse(
                "partials/_toast.html",
                {"request": request, "message": "Removed — won't watch similar emails.", "type": "success"},
            )
    db.commit()
    return request.app.state.templates.TemplateResponse(
        "partials/_toast.html",
        {"request": request, "message": "Feedback saved.", "type": "success"},
    )


@router.post("/api/v1/watched-threads/watch-inbox", response_class=HTMLResponse)
def watch_inbox_thread(
    request: Request,
    message_id: str = Form(...),
    conversation_id: str = Form(...),
    subject: str = Form(default=""),
    sender: str = Form(default=""),
    db: Session = Depends(get_db_dep),
):
    """Watch an incoming thread — e.g. someone else's conversation you want to follow."""
    watched_threads_service.add(
        db=db,
        graph_message_id=message_id,
        graph_conversation_id=conversation_id or None,
        subject=subject,
        recipient=sender,
        sent_at=datetime.utcnow(),
    )
    wt = db.query(watched_threads_service.WatchedThread).filter_by(graph_message_id=message_id).first()
    if wt:
        wt.source = "manual_inbox"
    db.commit()
    return request.app.state.templates.TemplateResponse(
        "partials/_toast.html",
        {"request": request, "message": "Watching thread for replies.", "type": "success"},
    )
