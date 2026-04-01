from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/history", response_class=HTMLResponse)
def history(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("history.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("settings.html", {"request": request})
