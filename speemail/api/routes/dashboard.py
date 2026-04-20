from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep, get_graph_dep
from speemail.auth.graph_auth import GraphClient
from speemail.models.tables import Task, TrackedEmail
from speemail.services import unresponded_service

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db_dep)):
    queue_count = db.query(TrackedEmail).filter_by(status="pending_approval").count()
    open_tasks = db.query(Task).filter(Task.status != "done").order_by(Task.created_at.desc()).limit(5).all()
    follow_ups = unresponded_service.get_awaiting_response(db, limit=5)
    return request.app.state.templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "queue_count": queue_count,
            "open_tasks": open_tasks,
            "follow_ups": follow_ups,
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
