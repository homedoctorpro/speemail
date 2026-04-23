from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import Task, TrackedEmail
from speemail.services import classification_service, unresponded_service, watched_threads_service

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    queue_count = db.query(TrackedEmail).filter_by(status="pending_approval").count()
    open_tasks = db.query(Task).filter(Task.status != "done").order_by(Task.created_at.desc()).limit(5).all()
    watched = watched_threads_service.get_active(db)
    alert_hours = watched_threads_service.get_alert_hours(db)
    # Returns instantly from cache (even stale). None only on very first ever load.
    needs_reply_cached = unresponded_service.get_needs_reply_cached(client, db, limit=10)
    return request.app.state.templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "queue_count": queue_count,
            "open_tasks": open_tasks,
            "watched_threads": watched,
            "alert_hours": alert_hours,
            "now_minus_hours": datetime.utcnow() - timedelta(hours=alert_hours),
            "needs_reply_cached": needs_reply_cached,
        },
    )


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    return request.app.state.templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return request.app.state.templates.TemplateResponse("history.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return request.app.state.templates.TemplateResponse("settings.html", {"request": request})


@router.get("/api/v1/debug/unresponded")
def debug_unresponded(
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    sent_data = client.get("/me/mailFolders/SentItems/messages", params={"$select": "conversationId,sentDateTime", "$top": "10"})
    inbox_data = client.get("/me/mailFolders/Inbox/messages", params={"$select": "id,subject,receivedDateTime,conversationId", "$top": "10"})
    inbox_data["value"] = sorted(inbox_data.get("value", []), key=lambda m: m.get("receivedDateTime", ""), reverse=True)
    sent_conv_dates: dict = {}
    for m in sent_data.get("value", []):
        c = m.get("conversationId", "")
        d = m.get("sentDateTime", "")
        if c not in sent_conv_dates or d > sent_conv_dates[c]:
            sent_conv_dates[c] = d
    inbox_analysis = []
    for msg in inbox_data.get("value", []):
        conv_id = msg.get("conversationId", "")
        received_dt = msg.get("receivedDateTime", "")
        last_sent = sent_conv_dates.get(conv_id)
        inbox_analysis.append({
            "subject": msg.get("subject"),
            "received": received_dt,
            "conv_id": conv_id[:20] + "...",
            "last_sent_in_conv": last_sent,
            "would_be_excluded": bool(last_sent and last_sent > received_dt),
        })
    return JSONResponse({"sent_sample_count": len(sent_data.get("value", [])), "inbox_sample": inbox_analysis})


@router.post("/api/v1/needs-reply/{message_id}/feedback", response_class=HTMLResponse)
def needs_reply_feedback(
    message_id: str,
    request: Request,
    decision: str = Form(...),
    reason: str = Form(default=""),
    subject: str = Form(default=""),
    sender_address: str = Form(default=""),
    sender_name: str = Form(default=""),
    body_preview: str = Form(default=""),
    db: Session = Depends(get_db_dep),
):
    classification_service.record_feedback(
        db=db,
        msg_id=message_id,
        decision=decision,
        reason=reason.strip() or None,
        subject=subject,
        sender_address=sender_address,
        sender_name=sender_name,
        body_preview=body_preview,
    )
    unresponded_service.invalidate_cache()
    if decision == "needs_reply":
        label = "Marked as needs reply"
    elif decision == "resolved":
        label = "Marked as resolved"
    else:
        label = "Skipped"
    return request.app.state.templates.TemplateResponse(
        "partials/_toast.html",
        {"request": request, "message": label, "type": "success"},
    )


@router.get("/api/v1/dashboard/needs-reply", response_class=HTMLResponse)
def needs_reply(
    request: Request,
    db: Session = Depends(get_db_dep),
    client: GraphClient = Depends(get_graph_dep),
):
    emails = unresponded_service.get_needs_reply(client, db, limit=10)
    return request.app.state.templates.TemplateResponse(
        "partials/_unresponded_list.html",
        {"request": request, "emails": emails, "section_type": "needs_reply"},
    )
