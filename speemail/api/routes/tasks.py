from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from speemail.api.deps import get_db_dep
from speemail.models.tables import Task

router = APIRouter(tags=["tasks"])


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db_dep)):
    open_tasks = db.query(Task).filter(Task.status != "done").order_by(Task.created_at.desc()).all()
    done_tasks = db.query(Task).filter_by(status="done").order_by(Task.updated_at.desc()).limit(20).all()
    return request.app.state.templates.TemplateResponse(
        "tasks.html",
        {"request": request, "open_tasks": open_tasks, "done_tasks": done_tasks},
    )


@router.get("/api/v1/tasks", response_class=HTMLResponse)
def list_tasks(request: Request, db: Session = Depends(get_db_dep)):
    open_tasks = db.query(Task).filter(Task.status != "done").order_by(Task.created_at.desc()).all()
    done_tasks = db.query(Task).filter_by(status="done").order_by(Task.updated_at.desc()).limit(20).all()
    return request.app.state.templates.TemplateResponse(
        "partials/_task_list.html",
        {"request": request, "open_tasks": open_tasks, "done_tasks": done_tasks},
    )


@router.post("/api/v1/tasks", response_class=HTMLResponse)
def create_task(
    request: Request,
    db: Session = Depends(get_db_dep),
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    due_date: str = Form(""),
):
    due = None
    if due_date:
        try:
            due = datetime.fromisoformat(due_date)
        except ValueError:
            pass

    task = Task(
        title=title.strip(),
        description=description.strip() or None,
        priority=priority,
        due_date=due,
    )
    db.add(task)
    db.flush()

    return request.app.state.templates.TemplateResponse(
        "partials/_task_card.html",
        {"request": request, "task": task},
    )


@router.patch("/api/v1/tasks/{task_id}", response_class=HTMLResponse)
def update_task_status(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db_dep),
    status: str = Form(...),
):
    task = db.get(Task, task_id)
    if not task:
        return Response(status_code=404)
    task.status = status
    db.flush()
    return request.app.state.templates.TemplateResponse(
        "partials/_task_card.html",
        {"request": request, "task": task},
    )


@router.delete("/api/v1/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db_dep)):
    task = db.get(Task, task_id)
    if task:
        db.delete(task)
    return Response(status_code=200, content="")
